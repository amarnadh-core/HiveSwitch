from __future__ import annotations

import json
import os
import socket
from dataclasses import asdict, dataclass
from pathlib import Path


DEFAULT_CONFIG_PATH = Path(".serverless-mc") / "config.json"
CONFIG_ENV_VAR = "SERVERLESS_MC_CONFIG"


@dataclass
class HelperConfig:
    player_name: str
    world_id: str
    session_id: str
    local_world_path: str
    cloud_root: str
    cache_dir: str
    server_dir: str
    server_jar: str
    java_path: str = "java"
    host_player: str = ""
    zerotier_network_id: str = ""
    helper_host: str = "127.0.0.1"
    helper_port: int = 8765
    server_host: str = "127.0.0.1"
    server_port: int = 25565
    server_label: str = "Serverless MC"

    @staticmethod
    def _normalize_path(raw: str) -> Path:
        """Normalize a path for the current platform.
        
        On Windows: /mnt/X/... -> X:\\...
        On Linux/WSL: X:\\... -> /mnt/x/...
        """
        import sys
        if sys.platform == "win32":
            if raw.startswith("/mnt/") and len(raw) >= 6:
                drive = raw[5].upper()
                rest = raw[6:] if len(raw) > 6 else ""
                return Path(f"{drive}:{rest}".replace("/", "\\")).resolve()
        else:
            if len(raw) >= 2 and raw[1] == ":" and raw[0].isalpha():
                drive = raw[0].lower()
                rest = raw[2:].replace("\\", "/")
                return Path(f"/mnt/{drive}{rest}").resolve()
        return Path(raw).expanduser().resolve()

    @property
    def local_world(self) -> Path:
        return self._normalize_path(self.local_world_path)

    @property
    def cloud(self) -> Path:
        return self._normalize_path(self.cloud_root)

    @property
    def cache(self) -> Path:
        return self._normalize_path(self.cache_dir)

    @property
    def server_workdir(self) -> Path:
        return self._normalize_path(self.server_dir)

    @property
    def server_jar_path(self) -> Path:
        return self._normalize_path(self.server_jar)

    @property
    def connect_address(self) -> str:
        return f"{self.server_host}:{self.server_port}"

    @staticmethod
    def is_wsl() -> bool:
        # WSL sets WSL_INTEROP; WSL_DISTRO_NAME is also common.
        return bool(os.environ.get("WSL_INTEROP") or os.environ.get("WSL_DISTRO_NAME"))

    @staticmethod
    def default_ipv4() -> str | None:
        # This does not require internet access; it just asks the OS for the chosen local address.
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except OSError:
            return None

    # -- Endpoint Discovery -------------------------------------------------

    def _detect_server_port(self) -> int:
        """Read actual listening port from server.properties if available."""
        props = self.server_workdir / "server.properties"
        if props.exists():
            try:
                for line in props.read_text(encoding="utf-8").splitlines():
                    if line.startswith("server-port="):
                        return int(line.split("=", 1)[1].strip())
            except (ValueError, OSError):
                pass
        return self.server_port  # Fall back to config

    @staticmethod
    def _probe_port(host: str, port: int, timeout: float = 2.0) -> bool:
        """Test if a TCP port is accepting connections."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((host, port))
            sock.close()
            return result == 0
        except OSError:
            return False

    @staticmethod
    def _windows_host_ip() -> str | None:
        """Get the Windows host IP from /etc/resolv.conf (WSL2 only).
        
        WSL2 sets the nameserver to the Windows host's virtual IP.
        This is the IP that Windows-side clients use to reach WSL services
        when localhost forwarding works.
        """
        try:
            resolv = Path("/etc/resolv.conf")
            if resolv.exists():
                for line in resolv.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("nameserver"):
                        ip = line.split()[1]
                        # Sanity: must look like an IP, not a hostname
                        if ip[0].isdigit():
                            return ip
        except OSError:
            pass
        return None

    def _verify_localhost_forwarding(self, port: int) -> bool:
        """Verify localhost forwarding from Windows client perspective.
        
        IMPORTANT: Probing the Windows host IP from WSL does NOT verify
        localhost forwarding — it only proves the virtual bridge works.
        Actual localhost forwarding (Windows 127.0.0.1 → WSL) uses a
        separate relay mechanism that can be broken independently.
        
        We cannot reliably test this from inside WSL. So we default to
        False unless the user explicitly opts in via env var.
        """
        if os.environ.get("SERVERLESS_MC_LOCALHOST_FWD", "").strip() == "1":
            # User explicitly declared localhost forwarding works
            # Do a basic sanity check that port is at least open locally
            if self._probe_port("127.0.0.1", port, timeout=1.0):
                return True
            print("[ENDPOINT] SERVERLESS_MC_LOCALHOST_FWD=1 but port not open locally")
            return False
        # Default: do not trust localhost forwarding
        return False

    @property
    def advertised_host(self) -> str:
        port = self._detect_server_port()

        if not self.is_wsl():
            # Windows: config address is correct
            return self.server_host

        # WSL: default to WSL VM IP (always reachable from Windows)
        # Only use 127.0.0.1 if user explicitly opts in
        if self._verify_localhost_forwarding(port):
            print(f"[ENDPOINT] localhost:{port} — forwarding confirmed by user — advertising 127.0.0.1")
            return "127.0.0.1"

        # WSL VM IP (reachable from Windows via virtual network)
        wsl_ip = self.default_ipv4()
        if wsl_ip:
            print(f"[ENDPOINT] Advertising WSL IP {wsl_ip}:{port}")
            return wsl_ip

        # Last resort
        print(f"[ENDPOINT] WARNING: No reachable endpoint found, using {self.server_host}")
        return self.server_host

    @property
    def advertised_address(self) -> str:
        port = self._detect_server_port()
        return f"{self.advertised_host}:{port}"


def default_config(workspace: Path) -> HelperConfig:
    workspace = workspace.resolve()
    return HelperConfig(
        player_name="player-one",
        world_id="prototype-world",
        session_id="local-session",
        local_world_path=str(workspace / "world"),
        cloud_root=str(workspace / ".local-cloud"),
        cache_dir=str(workspace / ".cache"),
        server_dir=str(workspace / ".server"),
        server_jar=str(workspace / "server.jar"),
        host_player="player-one",
    )


def current_config_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    configured = os.environ.get(CONFIG_ENV_VAR)
    if configured:
        return Path(configured)
    return DEFAULT_CONFIG_PATH


def load_config(path: Path | None = None) -> HelperConfig:
    path = current_config_path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}. Run `python -m helper_app.serverless_mc.cli init` first.")
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("server_host", "127.0.0.1")
    data.setdefault("server_port", 25565)
    data.setdefault("server_label", "Serverless MC")
    return HelperConfig(**data)


def save_config(config: HelperConfig, path: Path | None = None) -> None:
    path = current_config_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2) + "\n", encoding="utf-8")
