"""Cloud Relay Server configuration."""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path


DEFAULT_CONFIG_PATH = Path("cloud_relay.json")


@dataclass
class RelayConfig:
    """Configuration for the cloud relay server."""

    # Network
    host: str = "0.0.0.0"
    port: int = 9000

    # Storage
    data_dir: str = "relay_data"

    # Auth — shared secret that helpers must send as `Authorization: Bearer <key>`
    api_key: str = ""

    # Limits
    max_upload_bytes: int = 50 * 1024 * 1024  # 50 MB per file
    max_snapshots_per_world: int = 20

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()


def load_config(path: Path | None = None) -> RelayConfig:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Relay config not found: {path}. "
            f"Run `python -m cloud_relay.server init` to create one."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return RelayConfig(**data)


def save_config(config: RelayConfig, path: Path | None = None) -> None:
    path = path or DEFAULT_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")


def create_default_config(path: Path | None = None) -> RelayConfig:
    """Create a new config with a freshly generated API key."""
    config = RelayConfig(api_key=secrets.token_urlsafe(32))
    save_config(config, path)
    return config
