from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


SKIP_DIRS = {"session.lock"}


@dataclass(frozen=True)
class FileHash:
    path: str
    size: int
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(root: Path) -> list[FileHash]:
    if not root.exists():
        raise FileNotFoundError(f"World path does not exist: {root}")
    files: list[FileHash] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(root).as_posix()
        if rel == "session.lock":
            continue
        files.append(FileHash(path=rel, size=item.stat().st_size, sha256=sha256_file(item)))
    return files


def write_manifest(root: Path, manifest_path: Path) -> None:
    manifest = [item.__dict__ for item in build_manifest(root)]
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> list[FileHash]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [FileHash(**item) for item in data]


def validate_against_manifest(root: Path, manifest: list[FileHash]) -> list[str]:
    problems: list[str] = []
    for expected in manifest:
        actual_path = root / expected.path
        if not actual_path.exists():
            problems.append(f"missing: {expected.path}")
            continue
        actual_size = actual_path.stat().st_size
        if actual_size != expected.size:
            problems.append(f"size mismatch: {expected.path} expected={expected.size} actual={actual_size}")
            continue
        actual_hash = sha256_file(actual_path)
        if actual_hash != expected.sha256:
            problems.append(f"hash mismatch: {expected.path}")
    return problems


def diff_manifests(source: list[FileHash], target: list[FileHash]) -> list[FileHash]:
    """Returns files in the source manifest that are missing or different in the target manifest."""
    target_map = {item.path: item for item in target}
    changed: list[FileHash] = []
    for source_item in source:
        target_item = target_map.get(source_item.path)
        if not target_item or target_item.sha256 != source_item.sha256 or target_item.size != source_item.size:
            changed.append(source_item)
    return changed


