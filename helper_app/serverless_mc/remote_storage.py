"""HTTP-backed cloud storage client for the Cloud Relay Server.

Drop-in replacement for LocalCloudStorage — same public interface,
but all I/O goes through the relay's REST API instead of local filesystem.
"""

from __future__ import annotations

import json
import hashlib
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .integrity import FileHash, build_manifest, diff_manifests, sha256_file, validate_against_manifest
from .storage import SessionInfo, SnapshotInfo


class RemoteCloudStorage:
    """Cloud storage backed by the HiveSwitch Cloud Relay Server."""

    def __init__(self, relay_url: str, world_id: str, api_key: str = ""):
        # Normalize: strip trailing slash
        self.relay_url = relay_url.rstrip("/")
        self.world_id = world_id
        self.api_key = api_key

    # -- HTTP helpers -------------------------------------------------------

    def _request(self, method: str, path: str, body: bytes | None = None,
                 content_type: str = "application/json",
                 timeout: float = 30.0) -> tuple[int, bytes]:
        """Make an HTTP request to the relay. Returns (status, body_bytes)."""
        url = f"{self.relay_url}{path}"
        req = urllib.request.Request(url, method=method)
        req.add_header("User-Agent", "HiveSwitch-Helper/1.0")
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        if body is not None:
            req.add_header("Content-Type", content_type)
            req.data = body
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def _get_json(self, path: str) -> dict | list | None:
        status, body = self._request("GET", path)
        if status == 404:
            return None
        if status != 200:
            raise IOError(f"Relay GET {path} failed: HTTP {status}")
        return json.loads(body)

    def _put_json(self, path: str, data: Any) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        status, resp = self._request("PUT", path, body)
        if status not in (200, 201):
            raise IOError(f"Relay PUT {path} failed: HTTP {status} — {resp.decode()}")

    def _post_json(self, path: str, data: Any = None) -> tuple[int, dict]:
        body = json.dumps(data or {}, indent=2).encode("utf-8")
        status, resp = self._request("POST", path, body)
        parsed = json.loads(resp) if resp else {}
        if status not in (200, 201, 202, 409):
            raise IOError(f"Relay POST {path} failed: HTTP {status} — {resp.decode()}")
        return status, parsed

    def _get_binary(self, path: str, timeout: float = 60.0) -> bytes | None:
        status, body = self._request("GET", path, timeout=timeout)
        if status == 404:
            return None
        if status != 200:
            raise IOError(f"Relay GET {path} failed: HTTP {status}")
        return body

    def _put_binary(self, path: str, data: bytes) -> None:
        status, resp = self._request("PUT", path, data, content_type="application/octet-stream",
                                      timeout=120.0)
        if status not in (200, 201):
            raise IOError(f"Relay PUT {path} failed: HTTP {status} — {resp.decode()}")

    # -- URL builders -------------------------------------------------------

    def _world_path(self, suffix: str) -> str:
        return f"/worlds/{quote(self.world_id, safe='')}/{quote(suffix, safe='/')}"

    def _snap_path(self, snapshot_id: str, suffix: str) -> str:
        return (
            f"/worlds/{quote(self.world_id, safe='')}/snapshots/"
            f"{quote(snapshot_id, safe='')}/{quote(suffix, safe='/')}"
        )

    # -- Session ------------------------------------------------------------

    def session(self) -> SessionInfo:
        data = self._get_json(self._world_path("session"))
        if data is None:
            raise FileNotFoundError(f"No session metadata exists for world {self.world_id}")
        return SessionInfo(**data)

    def publish_session(self, session_id: str, current_host: str, server_address: str,
                        server_label: str, updated_by: str, state: str = "active",
                        handoff_snapshot: str | None = None) -> SessionInfo:
        info = SessionInfo(
            world_id=self.world_id, session_id=session_id,
            current_host=current_host, server_address=server_address,
            server_label=server_label,
            updated_at=datetime.now(timezone.utc).isoformat(),
            updated_by=updated_by, state=state, handoff_snapshot=handoff_snapshot,
        )
        self._put_json(self._world_path("session"), asdict(info))
        return info

    # -- Latest / Snapshots -------------------------------------------------

    def latest(self) -> SnapshotInfo:
        data = self._get_json(self._world_path("latest"))
        if data is None:
            raise FileNotFoundError(f"No snapshot exists for world {self.world_id}")
        data.setdefault("type", "full")
        data.setdefault("base", None)
        return SnapshotInfo(**data)

    def _get_snapshot_meta(self, snapshot_id: str) -> dict | None:
        return self._get_json(self._snap_path(snapshot_id, "meta"))

    def _get_snapshot_manifest(self, snapshot_id: str) -> list[FileHash] | None:
        data = self._get_json(self._snap_path(snapshot_id, "manifest"))
        if data is None:
            return None
        return [FileHash(**item) for item in data]

    # -- Snapshot chain -----------------------------------------------------

    def _build_snapshot_chain(self) -> list[SnapshotInfo]:
        MAX_DEPTH = 10
        chain: list[SnapshotInfo] = []
        snap = self.latest()

        for _ in range(MAX_DEPTH):
            meta = self._get_snapshot_meta(snap.snapshot_id)
            if meta is None:
                raise ValueError(f"Snapshot {snap.snapshot_id} metadata missing on relay")
            snap_type = meta.get("type", "full")
            snap_base = meta.get("base")
            snap = SnapshotInfo(
                world_id=snap.world_id, snapshot_id=snap.snapshot_id,
                created_at=snap.created_at, uploaded_by=snap.uploaded_by,
                archive=snap_type, manifest=f"snapshots/{snap.snapshot_id}/manifest.json",
                type=snap_type, base=snap_base,
            )
            chain.append(snap)
            if snap_type == "full" or snap_base is None:
                break
            # Follow the chain
            parent_meta = self._get_snapshot_meta(snap_base)
            if parent_meta is None:
                raise ValueError(f"Broken chain: {snap.snapshot_id} → missing base {snap_base}")
            snap = SnapshotInfo(
                world_id=self.world_id, snapshot_id=snap_base,
                created_at="", uploaded_by="", archive="",
                manifest=f"snapshots/{snap_base}/manifest.json",
                type=parent_meta.get("type", "full"), base=parent_meta.get("base"),
            )
        else:
            raise ValueError(f"Snapshot chain exceeds max depth {MAX_DEPTH}")

        chain.reverse()
        types = [("F" if s.type == "full" else "D") + s.snapshot_id[-6:] for s in chain]
        print(f"[CHAIN] {' -> '.join(types)}")
        return chain

    # -- Upload -------------------------------------------------------------

    def _new_snapshot_id(self) -> str:
        """Generate a unique snapshot ID with microseconds."""
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")

    @staticmethod
    def _file_hash(path: str, data: bytes) -> FileHash:
        return FileHash(path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest())

    def _should_compact(self) -> bool:
        try:
            chain = self._build_snapshot_chain()
        except Exception:
            return True
        delta_count = sum(1 for s in chain if s.type == "delta")
        if delta_count >= 5:
            return True
        last_full = next((s for s in chain if s.type == "full"), None)
        if last_full and last_full.created_at:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_full.created_at)).total_seconds()
                if age >= 300:
                    return True
            except Exception:
                pass
        return False

    def upload_world(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        if not world_path.exists():
            raise FileNotFoundError(f"World path does not exist: {world_path}")

        try:
            if self._should_compact():
                return self._upload_full(world_path, uploaded_by)
            else:
                return self._upload_delta(world_path, uploaded_by)
        except Exception:
            raise

    def _upload_full(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        snapshot_id = self._new_snapshot_id()
        local_manifest = build_manifest(world_path)
        print(f"[COMPACT] Uploading full snapshot {snapshot_id} ({len(local_manifest)} files)...")

        # Upload each file
        uploaded_manifest: list[FileHash] = []
        for item in local_manifest:
            src = world_path / item.path
            data = src.read_bytes()
            self._put_binary(self._snap_path(snapshot_id, f"files/world/{item.path}"), data)
            uploaded_manifest.append(self._file_hash(item.path, data))

        # Upload manifest + meta
        self._put_json(
            self._snap_path(snapshot_id, "manifest"),
            [asdict(h) for h in uploaded_manifest],
        )
        self._put_json(self._snap_path(snapshot_id, "meta"), {"type": "full", "base": None})

        # Mark complete
        self._post_json(self._snap_path(snapshot_id, "complete"))

        # Update latest pointer
        info = SnapshotInfo(
            world_id=self.world_id, snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            uploaded_by=uploaded_by, archive="world",
            manifest=f"snapshots/{snapshot_id}/manifest.json",
            type="full", base=None,
        )
        self._put_json(self._world_path("latest"), asdict(info))
        print(f"[COMPACT] Full snapshot {snapshot_id} complete")
        return info

    def _upload_delta(self, world_path: Path, uploaded_by: str) -> SnapshotInfo:
        latest_info = self.latest()
        remote_manifest = self._get_snapshot_manifest(latest_info.snapshot_id)
        if remote_manifest is None:
            print("[STORAGE] Cannot read remote manifest, forcing full")
            return self._upload_full(world_path, uploaded_by)

        local_manifest = build_manifest(world_path)
        changed_files = diff_manifests(local_manifest, remote_manifest)

        if not changed_files:
            print("[STORAGE] No changes detected, skipping snapshot")
            return latest_info

        snapshot_id = self._new_snapshot_id()
        print(f"Uploading {len(changed_files)} changed files (delta)...")

        # Upload changed files
        actual_hashes: dict[str, FileHash] = {}
        for item in changed_files:
            src = world_path / item.path
            data = src.read_bytes()
            self._put_binary(self._snap_path(snapshot_id, f"files/delta/{item.path}"), data)
            actual_hashes[item.path] = self._file_hash(item.path, data)

        # Build full manifest
        final_manifest = []
        for item in local_manifest:
            if item.path in actual_hashes:
                final_manifest.append(actual_hashes[item.path])
            else:
                final_manifest.append(item)

        # Upload manifest + meta
        self._put_json(
            self._snap_path(snapshot_id, "manifest"),
            [asdict(h) for h in final_manifest],
        )
        self._put_json(
            self._snap_path(snapshot_id, "meta"),
            {"type": "delta", "base": latest_info.snapshot_id},
        )

        # Mark complete
        self._post_json(self._snap_path(snapshot_id, "complete"))

        # Update latest pointer
        info = SnapshotInfo(
            world_id=self.world_id, snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc).isoformat(),
            uploaded_by=uploaded_by, archive="delta",
            manifest=f"snapshots/{snapshot_id}/manifest.json",
            type="delta", base=latest_info.snapshot_id,
        )
        self._put_json(self._world_path("latest"), asdict(info))
        return info

    # -- Download -----------------------------------------------------------

    def download_world(self, destination: Path) -> SnapshotInfo:
        destination.mkdir(parents=True, exist_ok=True)
        dirty_marker = destination / ".dirty"
        dirty_marker.touch(exist_ok=True)

        chain = self._build_snapshot_chain()

        for snap in chain:
            self._apply_snapshot(snap, destination)

        # Validate against latest manifest
        latest = chain[-1]
        remote_manifest = self._get_snapshot_manifest(latest.snapshot_id)
        if remote_manifest is None:
            raise ValueError(f"Cannot fetch manifest for {latest.snapshot_id}")

        # Prune extra files
        self._prune_extra_files(destination, remote_manifest)

        # Validate
        problems = validate_against_manifest(destination, remote_manifest)
        if problems:
            print(f"[VALIDATE] {len(problems)} issues after download — attempting re-download...")
            # Try to re-download the specific broken files
            manifest_map = {item.path: item for item in remote_manifest}
            for problem in problems:
                file_path = problem.split(": ", 1)[-1].split(" ")[0]
                # Search chain newest → oldest for this file
                for snap in reversed(chain):
                    sub = "world" if snap.type == "full" else "delta"
                    data = self._get_binary(self._snap_path(snap.snapshot_id, f"files/{sub}/{file_path}"))
                    if data is not None:
                        local = destination / file_path
                        local.parent.mkdir(parents=True, exist_ok=True)
                        local.write_bytes(data)
                        expected = manifest_map.get(file_path)
                        if expected and sha256_file(local) == expected.sha256:
                            print(f"[REPAIR] {file_path} restored from {snap.snapshot_id[-8:]}")
                            break

            remaining = validate_against_manifest(destination, remote_manifest)
            if remaining:
                raise ValueError(f"Download validation failed: {len(remaining)} files still corrupt")

        dirty_marker.unlink(missing_ok=True)
        return chain[-1]

    def _apply_snapshot(self, snap: SnapshotInfo, destination: Path) -> None:
        manifest = self._get_snapshot_manifest(snap.snapshot_id)
        if manifest is None:
            raise ValueError(f"Missing manifest for snapshot {snap.snapshot_id}")

        sub = "world" if snap.type == "full" else "delta"
        applied = 0
        skipped = 0

        for item in manifest:
            local = destination / item.path
            # Skip if already correct
            if local.exists():
                try:
                    if local.stat().st_size == item.size and sha256_file(local) == item.sha256:
                        skipped += 1
                        continue
                except OSError:
                    pass

            # Download from relay
            data = self._get_binary(self._snap_path(snap.snapshot_id, f"files/{sub}/{item.path}"))
            if data is not None:
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_bytes(data)
                applied += 1

        print(f"[APPLY] {snap.type}:{snap.snapshot_id[-8:]} — {applied} downloaded, {skipped} skipped")

    def _prune_extra_files(self, destination: Path, manifest: list[FileHash]) -> None:
        manifest_paths = {item.path for item in manifest}
        for local_file in sorted(destination.rglob("*"), reverse=True):
            if not local_file.is_file():
                continue
            rel = local_file.relative_to(destination).as_posix()
            if rel in (".dirty", "session.lock"):
                continue
            if rel not in manifest_paths:
                local_file.unlink()
        for local_dir in sorted(destination.rglob("*"), reverse=True):
            if local_dir.is_dir():
                try:
                    local_dir.rmdir()
                except OSError:
                    pass

    # -- Validate -----------------------------------------------------------

    def validate_world(self, world_path: Path) -> list[str]:
        info = self.latest()
        manifest = self._get_snapshot_manifest(info.snapshot_id)
        if manifest is None:
            raise ValueError(f"Cannot fetch manifest for {info.snapshot_id}")
        return validate_against_manifest(world_path, manifest)

    # -- Cleanup (server-side) ----------------------------------------------

    def cleanup_old_snapshots(self, keep_fulls: int = 2) -> None:
        # Cleanup is handled server-side when marking snapshots complete
        pass

    # -- Election -----------------------------------------------------------

    def try_election(self, candidate: str, **kwargs) -> tuple[bool, dict]:
        body = {"candidate": candidate, **kwargs}
        status, data = self._post_json(self._world_path("election"), body)
        return data.get("won", False), data.get("lock", {})

    def release_election(self, candidate: str) -> bool:
        body = json.dumps({"candidate": candidate}).encode("utf-8")
        status, resp = self._request("DELETE", self._world_path("election"), body)
        return status == 200
