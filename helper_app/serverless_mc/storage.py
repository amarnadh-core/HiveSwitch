from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .integrity import FileHash, load_manifest, validate_against_manifest, build_manifest, diff_manifests, sha256_file


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SnapshotInfo:
    world_id: str
    snapshot_id: str
    created_at: str
    uploaded_by: str
    archive: str
    manifest: str
    type: str = "full"
    base: str | None = None


@dataclass
class SessionInfo:
    world_id: str
    session_id: str
    current_host: str
    server_address: str
    server_label: str
    updated_at: str
    updated_by: str
    state: str = "active"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_cloud_path(raw_path: str | Path) -> Path:
    from .config import HelperConfig
    return HelperConfig._normalize_path(str(raw_path))


def _atomic_write_json(path: Path, data: dict, retries: int = 5, delay: float = 0.1) -> None:
    # Use unique temp name to avoid collisions between concurrent writers
    tmp_path = path.with_suffix(f".{os.getpid()}.tmp")
    content = json.dumps(data, indent=2) + "\n"
    tmp_path.write_text(content, encoding="utf-8")
    for attempt in range(retries):
        try:
            tmp_path.replace(path)
            return
        except (PermissionError, OSError) as e:
            if attempt < retries - 1:
                print(f"[STORAGE] {path.name} locked during write (attempt {attempt + 1}/{retries}); retrying...")
                time.sleep(delay * (attempt + 1))
            else:
                # Clean up temp file before raising
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
                raise


def _safe_read_json(path: Path, retries: int = 3, delay: float = 0.15) -> dict:
    last_error = None
    for attempt in range(retries):
        try:
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                raise json.JSONDecodeError("Empty file", "", 0)
            return json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = e
            if attempt < retries - 1:
                print(f"[STORAGE] {path.name} temporarily invalid (attempt {attempt + 1}/{retries}); retrying...")
                time.sleep(delay)
        except (PermissionError, OSError) as e:
            last_error = e
            if attempt < retries - 1:
                print(f"[STORAGE] {path.name} locked (attempt {attempt + 1}/{retries}); retrying...")
                time.sleep(delay)
        except FileNotFoundError:
            raise
    raise last_error  # type: ignore[misc]


def _is_process_alive(pid: int) -> bool:
    if sys.platform == "win32":
        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             capture_output=True, text=True, check=False)
        return str(pid) in res.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _extract_path_from_problem(problem: str) -> str:
    """Extract file path from a validation problem string."""
    # Formats: "missing: path", "size mismatch: path ...", "hash mismatch: path"
    parts = problem.split(": ", 1)
    if len(parts) < 2:
        return problem
    rest = parts[1]
    # "path expected=... actual=..." → take first token
    return rest.split(" ")[0]


# ---------------------------------------------------------------------------
# LocalCloudStorage
# ---------------------------------------------------------------------------

class LocalCloudStorage:
    """Filesystem-backed cloud storage with full/delta snapshots, repair, and rollback."""

    _io_lock = threading.Lock()

    def __init__(self, cloud_root: Path, world_id: str):
        self.cloud_root = _normalize_cloud_path(cloud_root)
        self.world_id = world_id
        self.world_root = self.cloud_root / "worlds" / self.world_id
        self.snapshot_root = self.world_root / "snapshots"
        self.latest_path = self.world_root / "latest.json"
        self.session_path = self.world_root / "session.json"

    # -- File lock (atomic, crash-safe, cross-platform) ---------------------

    def _acquire_file_lock(self) -> Path:
        lock = self.world_root / ".storage.lock"
        self.world_root.mkdir(parents=True, exist_ok=True)
        for attempt in range(30):
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, json.dumps({"pid": os.getpid(), "time": time.time()}).encode())
                os.close(fd)
                # Double-verify ownership (WSL+Windows edge case)
                try:
                    data = json.loads(lock.read_text())
                    if data.get("pid") != os.getpid():
                        raise RuntimeError("Lock ownership mismatch")
                except (json.JSONDecodeError, RuntimeError):
                    try:
                        lock.unlink()
                    except OSError:
                        pass
                    continue
                return lock
            except FileExistsError:
                try:
                    data = json.loads(lock.read_text())
                    if time.time() - data.get("time", 0) > 60 or not _is_process_alive(data.get("pid", -1)):
                        lock.unlink()
                        continue
                except Exception:
                    try:
                        lock.unlink()
                    except OSError:
                        pass
                    continue
                time.sleep(1)
        raise TimeoutError("Could not acquire storage lock after 30s")

    def _release_file_lock(self, lock: Path) -> None:
        try:
            lock.unlink()
        except OSError:
            pass

    # -- Snapshot metadata helpers ------------------------------------------

    def _load_snapshot_meta(self, snapshot_id: str) -> dict | None:
        snap_dir = self.snapshot_root / snapshot_id
        if not snap_dir.exists():
            return None
        if not (snap_dir / ".complete").exists():
            return None  # Incomplete snapshot
        meta_path = snap_dir / "meta.json"
        if meta_path.exists():
            return _safe_read_json(meta_path)
        return {"type": "full", "base": None}  # Legacy

    def _load_snapshot_by_id(self, snapshot_id: str) -> SnapshotInfo:
        snap_dir = self.snapshot_root / snapshot_id
        manifest_rel = f"snapshots/{snapshot_id}/manifest.json"
        meta = self._load_snapshot_meta(snapshot_id)
        if meta is None:
            raise ValueError(f"Snapshot {snapshot_id} is incomplete or missing")
        return SnapshotInfo(
            world_id=self.world_id,
            snapshot_id=snapshot_id,
            created_at="",
            uploaded_by="",
            archive=meta.get("type", "full"),
            manifest=manifest_rel,
            type=meta.get("type", "full"),
            base=meta.get("base"),
        )

    def _snapshot_data_dir(self, snap: SnapshotInfo) -> Path:
        snap_dir = self.snapshot_root / snap.snapshot_id
        world_dir = snap_dir / "world"
        delta_dir = snap_dir / "delta"
        if world_dir.exists():
            return world_dir
        if delta_dir.exists():
            return delta_dir
        # Legacy: raw_world
        raw = self.world_root / "raw_world"
        if raw.exists():
            return raw
        return snap_dir

    def _list_full_snapshots(self, newest_first: bool = False) -> list[SnapshotInfo]:
        fulls = []
        if not self.snapshot_root.exists():
            return fulls
        for d in sorted(self.snapshot_root.iterdir()):
            if not d.is_dir() or d.name.endswith(".tmp"):
                continue
            meta = self._load_snapshot_meta(d.name)
            if meta and meta.get("type", "full") == "full":
                fulls.append(self._load_snapshot_by_id(d.name))
        if newest_first:
            fulls.reverse()
        return fulls

    def _cleanup_tmp_dirs(self) -> None:
        if not self.snapshot_root.exists():
            return
        for d in self.snapshot_root.iterdir():
            if d.is_dir() and d.name.endswith(".tmp"):
                shutil.rmtree(d, ignore_errors=True)

    # -- Chain building -----------------------------------------------------

    def _build_snapshot_chain(self) -> list[SnapshotInfo]:
        MAX_DEPTH = 10
        chain: list[SnapshotInfo] = []
        snap = self.latest()

        for _ in range(MAX_DEPTH):
            snap_dir = self.snapshot_root / snap.snapshot_id
            if not (snap_dir / ".complete").exists():
                # Legacy snapshots without .complete are tolerated
                if not (snap_dir / "meta.json").exists():
                    snap = SnapshotInfo(**{**asdict(snap), "type": "full", "base": None})
                else:
                    raise ValueError(f"Incomplete snapshot {snap.snapshot_id}")
            chain.append(snap)
            if snap.type == "full" or snap.base is None:
                break
            # Verify base exists
            base_dir = self.snapshot_root / snap.base
            if not base_dir.exists():
                raise ValueError(f"Broken chain: {snap.snapshot_id} → missing base {snap.base}")
            snap = self._load_snapshot_by_id(snap.base)
        else:
            raise ValueError(f"Snapshot chain exceeds max depth {MAX_DEPTH}")

        chain.reverse()  # [full, d1, d2, ..., latest]
        ids = [s.snapshot_id for s in chain]
        types = [("F" if s.type == "full" else "D") + s.snapshot_id[-6:] for s in chain]
        print(f"[CHAIN] {' → '.join(types)}")
        return chain

    # -- Compaction decision ------------------------------------------------

    def _should_compact(self) -> bool:
        try:
            chain = self._build_snapshot_chain()
        except Exception:
            return True  # No valid chain, force full
        delta_count = sum(1 for s in chain if s.type == "delta")
        if delta_count >= 5:
            return True
        last_full = next((s for s in chain if s.type == "full"), None)
        if last_full and last_full.created_at:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_full.created_at)).total_seconds()
                if age >= 300:  # 5 minutes
                    return True
            except Exception:
                pass
        return False

    # -- Upload -------------------------------------------------------------

    def _check_upload_permission(self, uploaded_by: str) -> None:
        try:
            session = self.session()
            if session.current_host != uploaded_by:
                raise PermissionError(
                    f"Upload rejected: {uploaded_by} is not active host ({session.current_host})")
        except FileNotFoundError:
            pass  # First upload

    def upload_world(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        if not world_path.exists():
            raise FileNotFoundError(f"World path does not exist: {world_path}")

        with self._io_lock:
            lock = self._acquire_file_lock()
            try:
                self._check_upload_permission(uploaded_by)
                try:
                    if self._should_compact():
                        info = self._upload_full(world_path, uploaded_by)
                    else:
                        info = self._upload_delta(world_path, uploaded_by)
                except OSError as e:
                    self._cleanup_tmp_dirs()
                    if e.errno == errno.ENOSPC:
                        print("[STORAGE] DISK FULL — snapshot aborted")
                    raise
                self.cleanup_old_snapshots()
                return info
            finally:
                self._release_file_lock(lock)

    def _upload_full(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tmp_dir = self.snapshot_root / f"{snapshot_id}.tmp"
        final_dir = self.snapshot_root / snapshot_id
        world_dst = tmp_dir / "world"

        tmp_dir.mkdir(parents=True, exist_ok=True)
        world_dst.mkdir(parents=True, exist_ok=True)

        local_manifest = build_manifest(world_path)
        print(f"[COMPACT] Creating full snapshot {snapshot_id} ({len(local_manifest)} files)...")

        for item in local_manifest:
            src = world_path / item.path
            dst = world_dst / item.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # Write manifest
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps([asdict(item) for item in local_manifest], indent=2) + "\n",
            encoding="utf-8")

        # Write meta
        meta = {"type": "full", "base": None}
        (tmp_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        # Sanity check: verify all files exist and hash correctly
        for item in local_manifest:
            dst = world_dst / item.path
            if not dst.exists() or sha256_file(dst) != item.sha256:
                raise ValueError(f"Full snapshot sanity check failed: {item.path}")

        # Atomic rename
        tmp_dir.rename(final_dir)
        (final_dir / ".complete").touch()

        info = SnapshotInfo(
            world_id=self.world_id, snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            uploaded_by=uploaded_by, archive="world",
            manifest=f"snapshots/{snapshot_id}/manifest.json",
            type="full", base=None,
        )
        _atomic_write_json(self.latest_path, asdict(info))
        print(f"[COMPACT] Full snapshot {snapshot_id} complete")
        return info

    def _upload_delta(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        # Validate base
        latest_info = self.latest()
        base_dir = self.snapshot_root / latest_info.snapshot_id
        if not (base_dir / ".complete").exists() and (base_dir / "meta.json").exists():
            print("[STORAGE] Base snapshot incomplete, forcing full")
            return self._upload_full(world_path, uploaded_by)

        latest_manifest_path = self.world_root / latest_info.manifest.replace("\\", "/")
        remote_manifest = load_manifest(latest_manifest_path)
        local_manifest = build_manifest(world_path)
        changed_files = diff_manifests(local_manifest, remote_manifest)

        if not changed_files:
            print("[STORAGE] No changes detected, skipping snapshot")
            return latest_info

        snapshot_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        tmp_dir = self.snapshot_root / f"{snapshot_id}.tmp"
        final_dir = self.snapshot_root / snapshot_id
        delta_dst = tmp_dir / "delta"

        tmp_dir.mkdir(parents=True, exist_ok=True)
        delta_dst.mkdir(parents=True, exist_ok=True)

        print(f"Uploading {len(changed_files)} changed files (delta)...")
        for item in changed_files:
            src = world_path / item.path
            dst = delta_dst / item.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        # Write FULL manifest (not just delta)
        manifest_path = tmp_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps([asdict(item) for item in local_manifest], indent=2) + "\n",
            encoding="utf-8")

        # Write meta
        meta = {"type": "delta", "base": latest_info.snapshot_id}
        (tmp_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

        # Sanity check: verify changed files hash correctly
        for item in changed_files:
            dst = delta_dst / item.path
            if not dst.exists() or sha256_file(dst) != item.sha256:
                raise ValueError(f"Delta sanity check failed: {item.path}")

        # Atomic rename
        tmp_dir.rename(final_dir)
        (final_dir / ".complete").touch()

        info = SnapshotInfo(
            world_id=self.world_id, snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            uploaded_by=uploaded_by, archive="delta",
            manifest=f"snapshots/{snapshot_id}/manifest.json",
            type="delta", base=latest_info.snapshot_id,
        )
        _atomic_write_json(self.latest_path, asdict(info))
        return info

    # -- Legacy upload compatibility (raw_world backfill) -------------------

    def _backfill_raw_world(self, world_path: Path, local_manifest: list[FileHash]) -> None:
        """Keep raw_world/ in sync for backward compat with old snapshots."""
        raw_world_dir = self.world_root / "raw_world"
        if not raw_world_dir.exists():
            return
        for item in local_manifest:
            src = world_path / item.path
            dst = raw_world_dir / item.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or sha256_file(dst) != item.sha256:
                shutil.copy2(src, dst)

    # -- latest / session ---------------------------------------------------

    def latest(self) -> SnapshotInfo:
        if not self.latest_path.exists():
            raise FileNotFoundError(f"No snapshot exists for world {self.world_id}")
        data = _safe_read_json(self.latest_path)
        # Ensure type/base defaults for old snapshots
        data.setdefault("type", "full")
        data.setdefault("base", None)
        return SnapshotInfo(**data)

    def publish_session(self, session_id: str, current_host: str, server_address: str,
                        server_label: str, updated_by: str, state: str = "active") -> SessionInfo:
        self.world_root.mkdir(parents=True, exist_ok=True)
        info = SessionInfo(
            world_id=self.world_id, session_id=session_id,
            current_host=current_host, server_address=server_address,
            server_label=server_label,
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by, state=state,
        )
        _atomic_write_json(self.session_path, asdict(info))
        return info

    def session(self) -> SessionInfo:
        if not self.session_path.exists():
            raise FileNotFoundError(f"No session metadata exists for world {self.world_id}")
        data = _safe_read_json(self.session_path)
        return SessionInfo(**data)

    # -- Download -----------------------------------------------------------

    def download_world(self, destination: Path) -> SnapshotInfo:
        with self._io_lock:
            lock = self._acquire_file_lock()
            try:
                return self._download_world_locked(destination)
            finally:
                self._release_file_lock(lock)

    def _download_world_locked(self, destination: Path) -> SnapshotInfo:
        # Dirty marker for crash recovery
        destination.mkdir(parents=True, exist_ok=True)
        dirty_marker = destination / ".dirty"
        dirty_marker.touch(exist_ok=True)

        try:
            chain = self._build_snapshot_chain()
        except Exception:
            # Chain broken — try legacy raw_world download
            return self._download_legacy(destination)

        # Apply snapshots (hash-verified, local-aware)
        for snap in chain:
            self._apply_snapshot(snap, destination)

        # Get the latest manifest for validation
        latest = chain[-1]
        manifest_path = self.world_root / latest.manifest.replace("\\", "/")
        manifest = load_manifest(manifest_path)

        # Delete files not in manifest (file/dir cleanup)
        self._prune_extra_files(destination, manifest)

        # Validate → repair → rollback
        self._validate_or_recover(destination, chain, manifest)

        # Success — remove dirty marker
        dirty_marker.unlink(missing_ok=True)
        return latest

    def _download_legacy(self, destination: Path) -> SnapshotInfo:
        """Fallback for old-style raw_world snapshots without meta.json."""
        info = self.latest()
        manifest_path = self.world_root / info.manifest.replace("\\", "/")
        manifest = load_manifest(manifest_path)

        try:
            local_manifest = build_manifest(destination)
        except Exception:
            local_manifest = []

        changed = diff_manifests(manifest, local_manifest)
        destination.mkdir(parents=True, exist_ok=True)

        raw_world_dir = self.world_root / "raw_world"
        print(f"[LEGACY] Downloading {len(changed)} changed files from raw_world...")
        for item in changed:
            src = raw_world_dir / item.path
            dst = destination / item.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.exists():
                shutil.copy2(src, dst)

        problems = validate_against_manifest(destination, manifest)
        if problems:
            raise ValueError(f"Legacy download failed validation:\n" + "\n".join(problems))
        return info

    def _apply_snapshot(self, snap: SnapshotInfo, destination: Path) -> None:
        snap_dir = self.snapshot_root / snap.snapshot_id
        manifest_path = snap_dir / "manifest.json"

        if not manifest_path.exists():
            # Legacy: manifest in world_root path
            manifest_path = self.world_root / snap.manifest.replace("\\", "/")

        manifest = load_manifest(manifest_path)
        data_dir = self._snapshot_data_dir(snap)

        applied = 0
        skipped = 0
        for item in manifest:
            local = destination / item.path
            # Hash-verify before skipping
            if local.exists():
                try:
                    if local.stat().st_size == item.size and sha256_file(local) == item.sha256:
                        skipped += 1
                        continue
                except OSError:
                    pass  # File locked/inaccessible, re-download

            src = data_dir / item.path
            if src.exists():
                local.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, local)
                applied += 1

        print(f"[APPLY] {snap.type}:{snap.snapshot_id[-8:]} — {applied} copied, {skipped} skipped")

    def _prune_extra_files(self, destination: Path, manifest: list[FileHash]) -> None:
        """Remove files/dirs in destination that are not in the manifest."""
        manifest_paths = {item.path for item in manifest}
        # Remove extra files
        for local_file in sorted(destination.rglob("*"), reverse=True):
            if not local_file.is_file():
                continue
            rel = local_file.relative_to(destination).as_posix()
            if rel == ".dirty" or rel == "session.lock":
                continue
            if rel not in manifest_paths:
                local_file.unlink()
        # Remove empty directories
        for local_dir in sorted(destination.rglob("*"), reverse=True):
            if local_dir.is_dir():
                try:
                    local_dir.rmdir()  # Only removes if empty
                except OSError:
                    pass

    # -- Validate / Repair / Rollback ---------------------------------------

    def _validate_or_recover(self, destination: Path, chain: list[SnapshotInfo],
                              manifest: list[FileHash]) -> None:
        problems = validate_against_manifest(destination, manifest)
        if not problems:
            return

        print(f"[VALIDATE] {len(problems)} issues found, attempting repair...")
        if self._repair(destination, manifest, chain):
            return

        print("[VALIDATE] Repair failed, attempting rollback...")
        if self._rollback(destination):
            return

        raise ValueError(f"All recovery failed. {len(problems)} files still corrupt.")

    def _repair(self, destination: Path, manifest: list[FileHash],
                chain: list[SnapshotInfo]) -> bool:
        problems = validate_against_manifest(destination, manifest)
        if not problems:
            return True

        manifest_map = {item.path: item for item in manifest}
        print(f"[REPAIR] Fixing {len(problems)} files (1 attempt)...")

        for problem in problems:
            file_path = _extract_path_from_problem(problem)
            local = destination / file_path
            expected = manifest_map.get(file_path)

            if local.exists():
                corrupt_name = local.with_suffix(local.suffix + ".corrupt")
                try:
                    local.rename(corrupt_name)
                except OSError:
                    pass

            # Search newest → oldest for correct copy
            recovered = False
            for snap in reversed(chain):
                src = self._snapshot_data_dir(snap) / file_path
                if src.exists():
                    local.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, local)
                    # Verify the downloaded copy
                    if expected and sha256_file(local) == expected.sha256:
                        print(f"[REPAIR] {file_path} restored from {snap.snapshot_id[-8:]}")
                        recovered = True
                        break
                    else:
                        local.unlink(missing_ok=True)
                        continue

            if not recovered:
                print(f"[REPAIR] {file_path} — no valid copy found in any snapshot")

        remaining = validate_against_manifest(destination, manifest)
        if not remaining:
            print("[REPAIR] All files recovered")
            return True
        print(f"[REPAIR] {len(remaining)} files still corrupt")
        return False

    def _rollback(self, destination: Path) -> bool:
        backup = destination.with_name(destination.name + "_backup")
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            try:
                destination.rename(backup)
                print(f"[ROLLBACK] Backed up current world to {backup.name}")
            except OSError:
                shutil.rmtree(backup, ignore_errors=True)
                return False

        for older_full in self._list_full_snapshots(newest_first=True):
            try:
                destination.mkdir(parents=True, exist_ok=True)
                self._apply_snapshot(older_full, destination)
                snap_dir = self.snapshot_root / older_full.snapshot_id
                mpath = snap_dir / "manifest.json"
                if not mpath.exists():
                    mpath = self.world_root / older_full.manifest.replace("\\", "/")
                m = load_manifest(mpath)
                if not validate_against_manifest(destination, m):
                    print(f"[ROLLBACK] Recovered from snapshot {older_full.snapshot_id}")
                    shutil.rmtree(backup, ignore_errors=True)
                    return True
            except Exception:
                shutil.rmtree(destination, ignore_errors=True)
                continue

        # All failed — restore backup
        if backup.exists():
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            backup.rename(destination)
            print("[ROLLBACK] All snapshots corrupt. Restored backup.")
        return False

    # -- Cleanup ------------------------------------------------------------

    def cleanup_old_snapshots(self, keep_fulls: int = 2) -> None:
        try:
            session = self.session()
            if session.state == "migrating":
                return
        except (FileNotFoundError, PermissionError, OSError):
            pass

        reachable: set[str] = set()
        try:
            for snap in self._build_snapshot_chain():
                reachable.add(snap.snapshot_id)
        except Exception:
            return  # Can't determine safety

        all_fulls = self._list_full_snapshots()
        protected = reachable | {s.snapshot_id for s in all_fulls[-keep_fulls:]}

        if not self.snapshot_root.exists():
            return

        deleted = 0
        for d in sorted(self.snapshot_root.iterdir()):
            if d.is_dir() and d.name not in protected and not d.name.endswith(".tmp"):
                shutil.rmtree(d, ignore_errors=True)
                deleted += 1

        self._cleanup_tmp_dirs()
        if deleted:
            print(f"[CLEANUP] Removed {deleted} old snapshots")

    # -- Backward compat: validate_world ------------------------------------

    def validate_world(self, world_path: Path) -> list[str]:
        info = self.latest()
        manifest_rel = info.manifest.replace("\\", "/")
        return validate_against_manifest(world_path, load_manifest(self.world_root / manifest_rel))
