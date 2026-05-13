"""Relay-side storage operations.

Mirrors the directory layout of LocalCloudStorage but accessed through
the relay HTTP API rather than direct filesystem access.

Layout:
    data_dir/
      worlds/
        {world_id}/
          latest.json
          session.json
          election.lock
          snapshots/
            {snapshot_id}/
              meta.json
              manifest.json
              .complete
              world/       (full snapshots)
              delta/       (delta snapshots)
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class RelayStorage:
    """Filesystem backend for the relay server."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # -- Path helpers -------------------------------------------------------

    def world_dir(self, world_id: str) -> Path:
        return self.data_dir / "worlds" / world_id

    def session_path(self, world_id: str) -> Path:
        return self.world_dir(world_id) / "session.json"

    def latest_path(self, world_id: str) -> Path:
        return self.world_dir(world_id) / "latest.json"

    def election_path(self, world_id: str) -> Path:
        return self.world_dir(world_id) / "election.lock"

    def snapshot_dir(self, world_id: str, snapshot_id: str) -> Path:
        return self.world_dir(world_id) / "snapshots" / snapshot_id

    def snapshot_file(self, world_id: str, snapshot_id: str, rel_path: str) -> Path:
        """Resolve a file path inside a snapshot, preventing path traversal."""
        base = self.snapshot_dir(world_id, snapshot_id)
        target = (base / rel_path).resolve()
        if not str(target).startswith(str(base.resolve())):
            raise ValueError(f"Path traversal attempt: {rel_path}")
        return target

    # -- JSON helpers -------------------------------------------------------

    def read_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def write_json(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    # -- Session ------------------------------------------------------------

    def get_session(self, world_id: str) -> dict | None:
        return self.read_json(self.session_path(world_id))

    def put_session(self, world_id: str, data: dict) -> None:
        self.write_json(self.session_path(world_id), data)

    # -- Latest -------------------------------------------------------------

    def get_latest(self, world_id: str) -> dict | None:
        return self.read_json(self.latest_path(world_id))

    def put_latest(self, world_id: str, data: dict) -> None:
        self.write_json(self.latest_path(world_id), data)

    # -- Snapshots ----------------------------------------------------------

    def list_snapshots(self, world_id: str) -> list[str]:
        snap_root = self.world_dir(world_id) / "snapshots"
        if not snap_root.exists():
            return []
        return sorted(
            d.name for d in snap_root.iterdir()
            if d.is_dir() and not d.name.endswith(".tmp")
        )

    def get_snapshot_meta(self, world_id: str, snapshot_id: str) -> dict | None:
        return self.read_json(self.snapshot_dir(world_id, snapshot_id) / "meta.json")

    def put_snapshot_meta(self, world_id: str, snapshot_id: str, data: dict) -> None:
        self.write_json(self.snapshot_dir(world_id, snapshot_id) / "meta.json", data)

    def get_snapshot_manifest(self, world_id: str, snapshot_id: str) -> dict | list | None:
        path = self.snapshot_dir(world_id, snapshot_id) / "manifest.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def put_snapshot_manifest(self, world_id: str, snapshot_id: str, data: list | dict) -> None:
        path = self.snapshot_dir(world_id, snapshot_id) / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)

    def is_snapshot_complete(self, world_id: str, snapshot_id: str) -> bool:
        return (self.snapshot_dir(world_id, snapshot_id) / ".complete").exists()

    def mark_snapshot_complete(self, world_id: str, snapshot_id: str) -> None:
        marker = self.snapshot_dir(world_id, snapshot_id) / ".complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def get_file(self, world_id: str, snapshot_id: str, rel_path: str) -> bytes | None:
        """Read a binary file from a snapshot."""
        path = self.snapshot_file(world_id, snapshot_id, rel_path)
        if not path.exists():
            return None
        return path.read_bytes()

    def put_file(self, world_id: str, snapshot_id: str, rel_path: str,
                 data: bytes, max_bytes: int = 50 * 1024 * 1024) -> None:
        """Write a binary file into a snapshot."""
        if len(data) > max_bytes:
            raise ValueError(f"File too large: {len(data)} bytes (max {max_bytes})")
        path = self.snapshot_file(world_id, snapshot_id, rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    # -- Election -----------------------------------------------------------

    def try_election(self, world_id: str, candidate: dict) -> tuple[bool, dict]:
        """Attempt to acquire the election lock. Returns (won, lock_data)."""
        lock_path = self.election_path(world_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Check existing lock
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
                age = time.time() - float(existing.get("time", 0))
                if age < 60:
                    # Lock is still valid — election lost
                    return False, existing
                # Lock expired — fall through to acquire
            except (json.JSONDecodeError, OSError):
                pass  # Corrupt lock — overwrite

        # Write lock
        candidate["time"] = time.time()
        self.write_json(lock_path, candidate)

        # Verify we won (re-read)
        try:
            written = json.loads(lock_path.read_text(encoding="utf-8"))
            if written.get("candidate") == candidate.get("candidate"):
                return True, written
        except Exception:
            pass

        return False, candidate

    def release_election(self, world_id: str, candidate_name: str) -> bool:
        """Release the election lock if owned by the given candidate."""
        lock_path = self.election_path(world_id)
        if not lock_path.exists():
            return True
        try:
            data = json.loads(lock_path.read_text(encoding="utf-8"))
            if data.get("candidate") == candidate_name:
                lock_path.unlink()
                return True
            return False  # Not our lock
        except (json.JSONDecodeError, OSError):
            lock_path.unlink(missing_ok=True)
            return True

    def get_election(self, world_id: str) -> dict | None:
        return self.read_json(self.election_path(world_id))

    # -- Cleanup ------------------------------------------------------------

    def cleanup_snapshots(self, world_id: str, keep: int = 20) -> int:
        """Remove oldest snapshots beyond the keep limit. Returns count deleted."""
        snap_root = self.world_dir(world_id) / "snapshots"
        if not snap_root.exists():
            return 0

        all_dirs = sorted(
            d for d in snap_root.iterdir()
            if d.is_dir() and not d.name.endswith(".tmp")
        )

        if len(all_dirs) <= keep:
            return 0

        # Protect latest chain
        latest = self.get_latest(world_id)
        protected: set[str] = set()
        if latest:
            sid = latest.get("snapshot_id", "")
            # Walk the chain backwards
            for _ in range(10):
                if not sid:
                    break
                protected.add(sid)
                meta = self.get_snapshot_meta(world_id, sid)
                if meta is None or meta.get("type") == "full":
                    break
                sid = meta.get("base", "")

        to_delete = [
            d for d in all_dirs
            if d.name not in protected
        ]
        # Keep the newest ones
        to_delete = to_delete[:max(0, len(all_dirs) - keep)]

        import shutil
        deleted = 0
        for d in to_delete:
            shutil.rmtree(d, ignore_errors=True)
            deleted += 1
        return deleted
