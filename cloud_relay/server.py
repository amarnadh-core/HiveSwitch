"""Cloud Relay Server — main entry point.

Usage:
    python -m cloud_relay.server init          Create default config (cloud_relay.json)
    python -m cloud_relay.server               Start the relay server
    python -m cloud_relay.server --port 9000   Start on a specific port
    python -m cloud_relay.server --config X    Use a specific config file
"""

from __future__ import annotations

import argparse
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path

from .config import RelayConfig, create_default_config, load_config
from .handlers import make_handler
from .storage import RelayStorage


def cmd_init(args: argparse.Namespace) -> None:
    """Create a default cloud_relay.json config file."""
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"Config already exists: {path}")
        print("Use --force to overwrite.")
        return

    config = create_default_config(path)
    print(f"Created relay config: {path}")
    print(f"  Data dir:  {config.data_path}")
    print(f"  Port:      {config.port}")
    print(f"  API key:   {config.api_key}")
    print()
    print("Share the API key with your helper configs to authenticate.")
    print("Start the server with: python -m cloud_relay.server")


def cmd_serve(args: argparse.Namespace) -> None:
    """Start the relay server."""
    try:
        config = load_config(Path(args.config))
    except FileNotFoundError as e:
        print(str(e))
        sys.exit(1)

    # CLI overrides
    if args.port:
        config.port = args.port

    storage = RelayStorage(config.data_path)
    handler = make_handler(storage, config.api_key)

    server = ThreadingHTTPServer((config.host, config.port), handler)

    print("=" * 60)
    print("  HiveSwitch Cloud Relay Server")
    print("=" * 60)
    print(f"  Listening:  http://{config.host}:{config.port}")
    print(f"  Data dir:   {config.data_path}")
    print(f"  Auth:       {'API key required' if config.api_key else 'OPEN (no key)'}")
    print(f"  Health:     http://localhost:{config.port}/health")
    print("=" * 60)
    print()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down relay server...")
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cloud_relay.server",
        description="HiveSwitch Cloud Relay Server"
    )
    parser.add_argument(
        "--config", default="cloud_relay.json",
        help="Path to relay config file (default: cloud_relay.json)"
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="Override listen port."
    )

    sub = parser.add_subparsers(dest="command")

    # init
    init_parser = sub.add_parser("init", help="Create a default config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config.")

    # serve (explicit subcommand, same as default)
    sub.add_parser("serve", help="Start the relay server.")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    else:
        cmd_serve(args)


if __name__ == "__main__":
    main()
