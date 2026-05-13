"""
Network scanner for Serverless MC Helpers.

Discovers helpers on the local subnet and any extra IPs specified in the
active helper config (e.g. ZeroTier peers).  Run from the project root:

    python check_standbys.py
    python check_standbys.py --config .serverless-mc/viperf29.json
    python check_standbys.py --extra-ips 10.0.0.5 10.0.0.6
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import socket
import urllib.request
from pathlib import Path


# ---------------------------------------------------------------------------
# Config-driven peer discovery
# ---------------------------------------------------------------------------

def load_extra_ips_from_config(config_path: str | None) -> list[str]:
    """Pull ZeroTier or explicit peer IPs from a helper config file."""
    if config_path is None:
        # Try default locations
        candidates = [
            Path(".serverless-mc/config.json"),
            Path(".serverless-mc/viperf29.json"),
        ]
    else:
        candidates = [Path(config_path)]

    extra: list[str] = []
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # Collect helper_host if it is not localhost
            host = data.get("helper_host", "")
            if host and host not in ("127.0.0.1", "0.0.0.0", "localhost"):
                extra.append(host)
            # Collect any explicit peer list (future config field)
            for peer in data.get("peer_ips", []):
                extra.append(str(peer))
        except Exception:
            continue
    return extra


def get_local_subnet() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "192.168.1.1"
    finally:
        s.close()
    parts = local_ip.split(".")
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

def check_helper(ip: str, port: int = 8765) -> tuple[str, dict | None]:
    url = f"http://{ip}:{port}/session"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=1.5) as response:
            if response.status == 200:
                data = json.loads(response.read().decode("utf-8"))
                return ip, data
    except Exception:
        pass
    return ip, None


def scan_network(extra_ips: list[str] | None = None, port: int = 8765) -> None:
    subnet = get_local_subnet()
    print(f"Scanning {subnet} for Serverless MC Helpers (port {port})...")

    network = ipaddress.IPv4Network(subnet, strict=False)
    hosts = [str(ip) for ip in network.hosts()]

    # Always include localhost
    hosts.append("127.0.0.1")

    # Add any config-driven or CLI-supplied IPs
    if extra_ips:
        hosts.extend(extra_ips)

    hosts = list(set(hosts))
    found_any = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
        results = executor.map(lambda ip: check_helper(ip, port), hosts)

        for ip, data in results:
            if data is None:
                continue
            found_any = True

            player = data.get("player_name", "?")
            host = data.get("host_player", "?")
            state = data.get("state", "?")
            world = data.get("world_id", "?")
            is_host = data.get("is_host", False)

            if is_host and state == "active":
                badge = "🟢 ACTIVE HOST"
            elif state == "migrating":
                badge = "🟡 MIGRATING"
            elif state == "local":
                badge = "🔵 LOCAL (no cloud)"
            else:
                badge = "⚪ STANDBY"

            print(f"\n[+] {ip}")
            print(f"    Player: {player}  |  Host: {host}")
            print(f"    State:  {badge}")
            print(f"    World:  {world}")
            print(f"    Server: {data.get('server_address', '?')}")

    if not found_any:
        print("\nNo Serverless MC Helpers found on the network.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for Serverless MC Helpers on the network.")
    parser.add_argument("--config", default=None, help="Path to helper config JSON to read peer IPs from.")
    parser.add_argument("--extra-ips", nargs="*", default=[], help="Additional IPs to probe.")
    parser.add_argument("--port", type=int, default=8765, help="Helper API port (default: 8765).")
    args = parser.parse_args()

    config_ips = load_extra_ips_from_config(args.config)
    all_extra = list(set(config_ips + args.extra_ips))

    if all_extra:
        print(f"Extra IPs from config/CLI: {all_extra}")

    scan_network(extra_ips=all_extra, port=args.port)


if __name__ == "__main__":
    main()
