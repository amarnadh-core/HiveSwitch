from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import CONFIG_ENV_VAR, HelperConfig, current_config_path, default_config, load_config, make_storage, save_config
from .integrity import build_manifest, write_manifest
from .minecraft import (
    launch_hidden_server,
    load_server_properties,
    read_pid,
    set_auth_mode,
    set_rcon,
    set_server_port,
    write_eula,
    write_pid,
    write_server_properties,
)
from .rcon import RconClient
from .vpn import join_zerotier
from .web import _is_pid_alive, serve


def cmd_init(args: argparse.Namespace) -> None:
    workspace = Path.cwd()
    config = default_config(workspace)
    if args.player_name:
        config.player_name = args.player_name
        config.host_player = args.player_name
    if args.world_id:
        config.world_id = args.world_id
    save_config(config)
    print(f"Wrote {current_config_path()}")


def cmd_show_config(_: argparse.Namespace) -> None:
    config = load_config()
    for key, value in config.__dict__.items():
        print(f"{key}: {value}")


def cmd_connect_info(_: argparse.Namespace) -> None:
    config = load_config()
    print(f"Player: {config.player_name}")
    print(f"Host: {config.host_player}")
    print(f"Server entry: {config.server_label}")
    print(f"Minecraft address: {config.config_address}")
    if config.is_wsl():
        advertised = config.advertised_address
        print(f"WSL advertised address: {advertised}")
        if advertised != config.config_address:
            print(f"  (WSL IP detected — localhost forwarding appears unavailable)")
        else:
            print(f"  (localhost forwarding is working)")


def publish_current_session(state: str = "active") -> None:
    config = load_config()
    storage = make_storage(config)
    storage.publish_session(
        session_id=config.session_id,
        current_host=config.host_player,
        server_address=config.advertised_address,
        server_label=config.server_label,
        updated_by=config.player_name,
        state=state,
    )


def cmd_publish_session(args: argparse.Namespace) -> None:
    publish_current_session(state=args.state)
    config = load_config()
    print(f"Published session host {config.host_player} at {config.advertised_address}")


def cmd_session_info(_: argparse.Namespace) -> None:
    config = load_config()
    storage = make_storage(config)
    try:
        info = storage.session()
    except FileNotFoundError as exc:
        print(str(exc))
        return
    print(f"World: {info.world_id}")
    print(f"Session: {info.session_id}")
    print(f"Current host: {info.current_host}")
    print(f"Server entry: {info.server_label}")
    print(f"Minecraft address: {info.server_address}")
    print(f"State: {info.state}")
    print(f"Updated by: {info.updated_by}")
    print(f"Updated at: {info.updated_at}")
    if info.current_host == config.player_name:
        print(f"Local advertised address: {config.advertised_address}")


def cmd_create_profile(args: argparse.Namespace) -> None:
    base = load_config(Path(args.from_config)) if args.from_config else load_config()
    profile_root = Path(args.profile_root).expanduser().resolve() if args.profile_root else Path.cwd() / ".profiles" / args.player_name
    config = replace(
        base,
        player_name=args.player_name,
        host_player=args.host_player or base.host_player,
        local_world_path=str(profile_root / "world"),
        cache_dir=str(profile_root / "cache"),
        server_dir=str(profile_root),
        server_label=args.server_label or f"{args.player_name} host",
        server_port=args.server_port,
        helper_port=args.helper_port,
    )
    output = Path(args.output) if args.output else Path(".serverless-mc") / f"{args.player_name}.json"
    save_config(config, output)
    print(f"Wrote {output}")


def cmd_join_vpn(_: argparse.Namespace) -> None:
    config = load_config()
    print(join_zerotier(config.zerotier_network_id))


def cmd_configure_relay(args: argparse.Namespace) -> None:
    config = load_config()
    if not args.disable and not args.url:
        raise SystemExit("configure-relay requires --url unless --disable is used.")
    config.relay_url = "" if args.disable else args.url.rstrip("/")
    config.relay_api_key = "" if args.disable else (args.api_key or config.relay_api_key)
    save_config(config)
    if args.disable:
        print("Cloud relay disabled. Helper will use local filesystem storage.")
    else:
        print(f"Cloud relay set to {config.relay_url}")
        print("API key saved." if config.relay_api_key else "No API key saved; relay must allow open access.")


def cmd_upload_world(_: argparse.Namespace) -> None:
    config = load_config()
    storage = make_storage(config)
    info = storage.upload_world(config.local_world, config.player_name)
    print(f"Uploaded snapshot {info.snapshot_id} for {info.world_id}")


def _rcon_client_from_config() -> RconClient:
    config = load_config()
    properties = load_server_properties(config.server_workdir)
    if properties.get("enable-rcon") != "true":
        raise RuntimeError("RCON is not enabled. Run `auth-mode offline` if needed, then `configure-rcon` and restart.")
    password = properties.get("rcon.password", "")
    if not password:
        raise RuntimeError("RCON password is empty. Run `configure-rcon` and restart.")
    port = int(properties.get("rcon.port", "25575"))
    return RconClient("127.0.0.1", port, password)


def cmd_configure_rcon(args: argparse.Namespace) -> None:
    config = load_config()
    password = args.password or f"smc-{secrets.token_urlsafe(18)}"
    path = set_rcon(config.server_workdir, enabled=not args.disable, password=password, port=args.port)
    state = "enabled" if not args.disable else "disabled"
    print(f"RCON {state} in {path}. Restart the Minecraft server for this to take effect.")


def cmd_configure_server(args: argparse.Namespace) -> None:
    config = load_config()
    write_server_properties(config.server_workdir)
    set_server_port(config.server_workdir, args.port, args.server_ip)
    config.server_host = args.connect_host
    config.server_port = args.port
    if args.label:
        config.server_label = args.label
    save_config(config)
    if args.accept_eula:
        write_eula(config.server_workdir, accepted=True)
        eula_message = "EULA accepted in eula.txt."
    else:
        eula_path = config.server_workdir / "eula.txt"
        eula_message = f"EULA not changed. If this is a new server folder, review and accept {eula_path} before launch."
    print(f"Configured server folder {config.server_workdir} on port {args.port}. {eula_message}")


def cmd_server_command(args: argparse.Namespace) -> None:
    with _rcon_client_from_config() as client:
        response = client.command(args.command)
    print(response or "OK")


def cmd_save_world(_: argparse.Namespace) -> None:
    with _rcon_client_from_config() as client:
        client.command("save-all flush")
    print("World save flushed.")


def cmd_sync_world(_: argparse.Namespace) -> None:
    config = load_config()
    snapshot_id = sync_current_world()
    print(f"Saved and uploaded snapshot {snapshot_id} for {config.world_id}")


def upload_current_world() -> str:
    config = load_config()
    storage = make_storage(config)
    info = storage.upload_world(config.local_world, config.player_name)
    return info.snapshot_id


def sync_current_world() -> str:
    config = load_config()
    staging_dir = config.server_workdir / ".staging_world"
    
    # Phase 1: Freeze and clone (minimize Minecraft pause)
    with _rcon_client_from_config() as client:
        client.command("save-off")
        client.command("save-all flush")
        time.sleep(0.5)  # Brief settle
        try:
            if staging_dir.exists():
                import shutil
                shutil.rmtree(staging_dir)
            import shutil
            shutil.copytree(config.local_world, staging_dir, ignore=shutil.ignore_patterns("session.lock"))
        finally:
            client.command("save-on")
            
    # Phase 2: Async upload from the immutable mirror
    storage = make_storage(config)
    info = storage.upload_world(staging_dir, config.player_name)
    return info.snapshot_id


def cmd_download_world(args: argparse.Namespace) -> None:
    config = load_config()
    destination = Path(args.destination).expanduser().resolve() if args.destination else config.local_world
    storage = make_storage(config)
    info = storage.download_world(destination)
    print(f"Downloaded snapshot {info.snapshot_id} to {destination}")


def cmd_validate_cache(args: argparse.Namespace) -> None:
    config = load_config()
    world_path = Path(args.path).expanduser().resolve() if args.path else config.local_world
    storage = make_storage(config)
    problems = storage.validate_world(world_path)
    if problems:
        print("Cache validation failed:")
        for problem in problems:
            print(f"- {problem}")
        raise SystemExit(1)
    print("Cache validation passed.")


def validate_config_world(config_path: Path) -> None:
    config = load_config(config_path)
    storage = make_storage(config)
    problems = storage.validate_world(config.local_world)
    if problems:
        joined = "\n".join(f"- {problem}" for problem in problems)
        raise RuntimeError(f"Cache validation failed for {config_path}:\n{joined}")


def cmd_manifest(args: argparse.Namespace) -> None:
    world_path = Path(args.path).expanduser().resolve() if args.path else load_config().local_world
    if args.output:
        write_manifest(world_path, Path(args.output).expanduser().resolve())
        print(f"Wrote manifest for {len(build_manifest(world_path))} files.")
    else:
        for item in build_manifest(world_path):
            print(f"{item.sha256}  {item.path}")


def cmd_set_host(args: argparse.Namespace) -> None:
    config = load_config()
    config.host_player = args.player_name
    save_config(config)
    print(f"Host set to {args.player_name}")


def cmd_auth_mode(args: argparse.Namespace) -> None:
    config = load_config()
    path = set_auth_mode(config.server_workdir, args.mode)
    if args.mode == "offline":
        print(f"Set {path} for offline/TLauncher-compatible private testing.")
        print("Restart the Minecraft server for this to take effect.")
    else:
        print(f"Set {path} for Microsoft/Mojang online authentication.")
        print("Restart the Minecraft server for this to take effect.")


def cmd_launch_server(args: argparse.Namespace) -> None:
    config = load_config()
    write_server_properties(config.server_workdir)
    
    # H3 Invariant: verify no owned Java server already running
    existing = read_pid(config.server_workdir)
    if existing and _is_pid_alive(existing.get("pid", -1)):
        owner = existing.get("host_player", "")
        if owner == config.player_name:
            print(f"ERROR: You already have a running server (pid={existing['pid']}). Refusing to launch.")
            return
        else:
            print(f"WARNING: Found foreign server pid={existing['pid']} (owner={owner}). Launching anyway may cause corruption.")

    # Upload current world to cloud BEFORE launch so standby has latest state
    storage = make_storage(config)
    try:
        info = storage.upload_world(config.local_world, config.player_name)
        print(f"Synced world to cloud (snapshot {info.snapshot_id}) before launch.")
    except Exception as e:
        print(f"Warning: Could not sync world to cloud: {e}")

    process = launch_hidden_server(config.java_path, config.server_jar_path, config.server_workdir, args.memory)
    pid = process.pid
    write_pid(config.server_workdir, pid, host_player=config.host_player)
    # publish_current_session()  # B1: Removed to prevent CLI from hijacking migration state
    print(f"Launched hidden server process pid={pid}")


def cmd_server_status(_: argparse.Namespace) -> None:
    config = load_config()
    pid_data = read_pid(config.server_workdir)
    if pid_data is None:
        print("No helper-managed server pid file found.")
        return
    print(f"Helper-managed server pid={pid_data['pid']} (platform: {pid_data.get('platform', 'unknown')})")


def cmd_stop_server(args: argparse.Namespace) -> None:
    config = load_config()
    pid_data = read_pid(config.server_workdir)
    if pid_data is None:
        print("No helper-managed server pid file found.")
        return
        
    pid = pid_data["pid"]
    platform = pid_data.get("platform", "legacy")
    
    if platform != "legacy" and platform != sys.platform:
        print(f"WARNING: PID file was written by platform '{platform}', but we are on '{sys.platform}'.")
        print("Skipping kill to avoid terminating the wrong process on this OS.")
        return

    def is_pid_running(p: int) -> bool:
        if sys.platform == "win32":
            res = subprocess.run(["tasklist", "/FI", f"PID eq {p}"], check=False, capture_output=True, text=True)
            return str(p) in res.stdout
        else:
            try:
                os.kill(p, 0)
                return True
            except OSError:
                return False

    def kill_pid(p: int) -> tuple[bool, str]:
        if sys.platform == "win32":
            res = subprocess.run(["taskkill", "/PID", str(p), "/T", "/F"], check=False, capture_output=True, text=True)
            return res.returncode == 0, (res.stdout or res.stderr).strip()
        else:
            try:
                import signal
                os.kill(p, signal.SIGKILL)
                return True, "Killed successfully via SIGKILL."
            except OSError as e:
                return False, str(e)
                
    try:
        with _rcon_client_from_config() as client:
            client.command("save-all flush")
            client.command("stop")
        for _ in range(30):
            if not is_pid_running(pid):
                print(f"Gracefully stopped helper-managed server pid={pid}")
                _remove_pid_file(config.server_workdir)
                return
            time.sleep(1)
        print("Stop command sent, but the process is still visible. Use --force if needed.")
        return
    except Exception as exc:
        if not args.force:
            print(f"Graceful stop failed: {exc}")
            print("Run stop-server --force to terminate the helper-managed process.")
            return

    success, msg = kill_pid(pid)
    if success:
        print(f"Force-stopped helper-managed server pid={pid}")
        _remove_pid_file(config.server_workdir)
    else:
        print(f"Force-stop failed: {msg}")


def _remove_pid_file(server_workdir: Path) -> None:
    from .minecraft import pid_file
    try:
        pid_file(server_workdir).unlink()
    except OSError:
        pass


def stop_current_server(force: bool = False) -> None:
    args = argparse.Namespace(force=force)
    cmd_stop_server(args)


def cmd_manual_switch(args: argparse.Namespace) -> None:
    source_config_path = current_config_path()
    target_config_path = Path(args.to_config)

    print(f"Source config: {source_config_path}")
    print(f"Target config: {target_config_path}")

    source_config = load_config()
    target_config = load_config(target_config_path)
    to_host = args.to_host or target_config.player_name

    storage = make_storage(source_config)
    try:
        session = storage.session()
        if session.current_host != source_config.player_name:
            print(f"ERROR: You are trying to migrate FROM {source_config.player_name}, but the active host is {session.current_host}.")
            print(f"Did you forget to set SERVERLESS_MC_CONFIG or pass --config?")
            sys.exit(1)
    except FileNotFoundError:
        pass  # First run, no session yet
    if args.skip_source_stop:
        print("Skipping source stop.")
        snapshot_id = None
        if args.skip_source_sync:
            print("Skipping source sync.")
        else:
            snapshot_id = sync_current_world()
            print(f"Source saved and uploaded live snapshot {snapshot_id}.")
    else:
        # During migration: best-effort save, then force-kill (no RCON-first hang)
        try:
            with _rcon_client_from_config() as rcon:
                rcon.command("save-all flush")
            time.sleep(0.2)  # Brief settle for save flush
        except Exception:
            pass  # RCON may be unavailable — that's fine during migration
        from .web import _force_kill_local_server
        _force_kill_local_server(source_config, only_if_owned=True)
        print("Source server stopped.")
        snapshot_id = None
        if args.skip_source_sync:
            print("Skipping source sync.")
        else:
            snapshot_id = upload_current_world()
            print(f"Source stopped and uploaded snapshot {snapshot_id}.")

    # Transactional handoff: publish migrating state WITH the required snapshot
    try:
        storage.publish_session(
            session_id=source_config.session_id,
            current_host=to_host,
            server_address="",
            server_label=target_config.server_label or source_config.server_label,
            updated_by=source_config.player_name,
            state="migrating",
            handoff_snapshot=snapshot_id
        )
        print(f"Session state published as: migrating (target={to_host}, snapshot={snapshot_id})")
    except Exception as e:
        print(f"Could not set migrating state: {e}")

    # Re-load target config (source sync may have changed cloud state)
    target_config = load_config(target_config_path)
    storage = make_storage(target_config)
    info = storage.download_world(target_config.local_world)
    print(f"Target downloaded snapshot {info.snapshot_id}.")

    validate_config_world(target_config_path)
    print("Target cache validation passed.")

    target_config = replace(target_config, host_player=to_host)
    save_config(target_config, target_config_path)
    print(f"Target host set to {to_host}.")

    # Bug 4: Detect cross-platform launch (e.g. running on WSL but target is Windows config)
    if args.launch:
        source_is_wsl = HelperConfig.is_wsl()
        target_is_wsl = target_config.server_dir.startswith("/mnt/") or target_config.server_dir.startswith("/home/")
        source_is_windows = (sys.platform == "win32")
        target_is_windows = len(target_config.server_dir) >= 2 and target_config.server_dir[1] == ":"

        cross_platform = (source_is_wsl and target_is_windows) or (source_is_windows and target_is_wsl)

        if cross_platform:
            print(f"\n[WARNING] Cross-platform launch detected!")
            print(f"  Source platform: {'WSL' if source_is_wsl else 'Windows'}")
            print(f"  Target config:   {target_config_path}")
            print(f"  Target server:   {target_config.server_dir}")
            print(f"  Cannot launch a {'Windows' if target_is_windows else 'WSL'} server from {'WSL' if source_is_wsl else 'Windows'}.")
            print(f"\n  The target standby helper should auto-promote.")
            print(f"  If not, start the target helper with: serve --allow-host-promotion")

            # Publish session pointing to target so standby can detect the handoff
            # Use empty server_address — standby will fill in its own address on promotion
            storage.publish_session(
                session_id=target_config.session_id,
                current_host=target_config.host_player,
                server_address="",
                server_label=target_config.server_label,
                updated_by=source_config.player_name,
                state="migrating",
                handoff_snapshot=snapshot_id
            )
            print("Session published as migrating (standby should auto-promote).")
            return

    if not args.launch:
        # Bug 3: Without --launch, stop heartbeat so standby detects host as dead
        print("\nLaunch skipped. Publishing session for standby promotion...")
        storage.publish_session(
            session_id=target_config.session_id,
            current_host=target_config.host_player,
            server_address="",
            server_label=target_config.server_label,
            updated_by=source_config.player_name,
            state="migrating",
            handoff_snapshot=snapshot_id
        )
        print("Session published as migrating. Target standby helper should auto-promote.")
        print("If not, start the target helper with: serve --allow-host-promotion")
        return

    # Port: CLI override → server.properties → config default
    if args.port is not None:
        target_port = args.port
        write_server_properties(target_config.server_workdir)
        set_server_port(target_config.server_workdir, target_port)
        target_config = replace(target_config, server_port=target_port)
        save_config(target_config, target_config_path)
        print(f"Target server port set to {target_port} (CLI override).")
    else:
        target_port = target_config._detect_server_port()
        print(f"Target server port: {target_port} (auto-detected).")

    process = launch_hidden_server(target_config.java_path, target_config.server_jar_path, target_config.server_workdir, args.memory)
    write_pid(target_config.server_workdir, process.pid, host_player=target_config.host_player)
    storage.publish_session(
        session_id=target_config.session_id,
        current_host=target_config.host_player,
        server_address=target_config.advertised_address,
        server_label=target_config.server_label,
        updated_by=target_config.player_name,
    )
    print(f"Launched target server pid={process.pid}, endpoint={target_config.advertised_address}")


def cmd_serve(args: argparse.Namespace) -> None:
    serve(load_config(), allow_host_promotion=args.allow_host_promotion)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="serverless-mc")
    parser.add_argument("--config", help="Use a specific helper config file.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a local helper config.")
    init.add_argument("--player-name")
    init.add_argument("--world-id")
    init.set_defaults(func=cmd_init)

    show = sub.add_parser("show-config", help="Print active helper config.")
    show.set_defaults(func=cmd_show_config)

    connect = sub.add_parser("connect-info", help="Print Minecraft connection target for this profile.")
    connect.set_defaults(func=cmd_connect_info)

    session = sub.add_parser("session-info", help="Print shared cloud session metadata.")
    session.set_defaults(func=cmd_session_info)

    publish = sub.add_parser("publish-session", help="Publish this profile as the current session host.")
    publish.add_argument("--state", default="active")
    publish.set_defaults(func=cmd_publish_session)

    profile = sub.add_parser("create-profile", help="Create a separate local player profile config.")
    profile.add_argument("player_name")
    profile.add_argument("--output")
    profile.add_argument("--from-config")
    profile.add_argument("--profile-root")
    profile.add_argument("--host-player")
    profile.add_argument("--server-label")
    profile.add_argument("--server-port", type=int, default=25565)
    profile.add_argument("--helper-port", type=int, default=8765)
    profile.set_defaults(func=cmd_create_profile)

    vpn = sub.add_parser("join-vpn", help="Join configured ZeroTier network.")
    vpn.set_defaults(func=cmd_join_vpn)

    relay = sub.add_parser("configure-relay", help="Enable or disable HTTP Cloud Relay storage.")
    relay.add_argument("--url", required=False, help="Relay base URL, for example http://example.com:9000")
    relay.add_argument("--api-key", default=None)
    relay.add_argument("--disable", action="store_true")
    relay.set_defaults(func=cmd_configure_relay)

    upload = sub.add_parser("upload-world", help="Upload local world to phase-one storage.")
    upload.set_defaults(func=cmd_upload_world)

    sync = sub.add_parser("sync-world", help="Flush server save through RCON, then upload the world.")
    sync.set_defaults(func=cmd_sync_world)

    download = sub.add_parser("download-world", help="Download latest world snapshot.")
    download.add_argument("--destination")
    download.set_defaults(func=cmd_download_world)

    validate = sub.add_parser("validate-cache", help="Validate a local world against the latest cloud manifest.")
    validate.add_argument("--path")
    validate.set_defaults(func=cmd_validate_cache)

    manifest = sub.add_parser("manifest", help="Print or write SHA-256 manifest for a world folder.")
    manifest.add_argument("--path")
    manifest.add_argument("--output")
    manifest.set_defaults(func=cmd_manifest)

    host = sub.add_parser("set-host", help="Manually select the current host.")
    host.add_argument("player_name")
    host.set_defaults(func=cmd_set_host)

    auth = sub.add_parser("auth-mode", help="Switch server authentication mode.")
    auth.add_argument("mode", choices=["online", "offline"])
    auth.set_defaults(func=cmd_auth_mode)

    rcon = sub.add_parser("configure-rcon", help="Enable or disable RCON for graceful save/stop.")
    rcon.add_argument("--password")
    rcon.add_argument("--port", type=int, default=25575)
    rcon.add_argument("--disable", action="store_true")
    rcon.set_defaults(func=cmd_configure_rcon)

    server = sub.add_parser("configure-server", help="Prepare server.properties for this helper profile.")
    server.add_argument("--port", type=int, default=25565)
    server.add_argument("--server-ip", default="")
    server.add_argument("--connect-host", default="127.0.0.1")
    server.add_argument("--label")
    server.add_argument("--accept-eula", action="store_true")
    server.set_defaults(func=cmd_configure_server)

    command = sub.add_parser("server-command", help="Send one RCON command to the Minecraft server.")
    command.add_argument("command")
    command.set_defaults(func=cmd_server_command)

    save = sub.add_parser("save-world", help="Flush Minecraft world save through RCON.")
    save.set_defaults(func=cmd_save_world)

    launch = sub.add_parser("launch-server", help="Launch the configured Minecraft server jar hidden.")
    launch.add_argument("--memory", default="2G")
    launch.set_defaults(func=cmd_launch_server)

    status = sub.add_parser("server-status", help="Show helper-managed server process pid.")
    status.set_defaults(func=cmd_server_status)

    stop = sub.add_parser("stop-server", help="Stop the helper-managed server process.")
    stop.add_argument("--force", action="store_true")
    stop.set_defaults(func=cmd_stop_server)

    switch = sub.add_parser("manual-switch", help="Run the phase-one manual host-switch workflow.")
    switch.add_argument("--to-config", required=True, help="Path to target host's config file.")
    switch.add_argument("--to-host", default=None, help="Target host player name. Inferred from target config if omitted.")
    switch.add_argument("--port", type=int, default=None, help="Server port override. Auto-detected from server.properties if omitted.")
    switch.add_argument("--memory", default="2G")
    switch.add_argument("--launch", action="store_true")
    switch.add_argument("--skip-source-sync", action="store_true")
    switch.add_argument("--skip-source-stop", action="store_true")
    switch.set_defaults(func=cmd_manual_switch)

    web = sub.add_parser("serve", help="Run localhost HTTP API and reconnect screen.")
    web.add_argument("--allow-host-promotion", action="store_true", help="Automatically launch the host server if an election is won.")
    web.set_defaults(func=cmd_serve)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.config:
        os.environ[CONFIG_ENV_VAR] = args.config
    args.func(args)


if __name__ == "__main__":
    main()
