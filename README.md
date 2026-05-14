# HiveSwitch — Serverless Minecraft

A "serverless" Minecraft multiplayer system where one player's machine hosts a hidden vanilla dedicated server. World data is automatically synced to a shared cloud, and host control can be transferred between machines — manually or automatically via leader election.

## Features

- **Delta Snapshots** — Only changed region files are uploaded/downloaded, not the entire world.
- **Leader Election** — Standby helpers detect host death via heartbeat and automatically promote.
- **Fabric Client Mod** — Zero-touch in-game reconnection during host migrations.
- **Cross-Platform** — Windows ↔ WSL ↔ Linux host migration.
- **ZeroTier Support** — Multi-network play via `join-vpn`.
- **Cloud Relay** — Optional HTTP storage/session/election backend for machines that cannot share `.local-cloud/`.

By default the storage layer uses `.local-cloud/` on a shared filesystem. For different networks or laptops without file sharing, run the Cloud Relay Server and configure helpers with `configure-relay`.

## Quick Start

```powershell
python -m helper_app.serverless_mc.cli init --player-name Alice --world-id demo-world
python -m helper_app.serverless_mc.cli show-config
```

Edit `.serverless-mc/config.json` and set:

- `local_world_path` to the world folder you want to share
- `server_jar` to a Fabric server jar (1.20.1)
- `zerotier_network_id` to your network ID, if ZeroTier is installed
- `cloud_root` to a shared folder both players can access

Start the helper API with automatic host promotion:

```powershell
python -m helper_app.serverless_mc.cli serve --allow-host-promotion
```

Open `http://127.0.0.1:8765/reconnect` for the reconnect screen.

## Common Commands

```powershell
python -m helper_app.serverless_mc.cli join-vpn
python -m helper_app.serverless_mc.cli configure-relay --url http://relay-host:9000 --api-key <key>
python -m helper_app.serverless_mc.cli connect-info
python -m helper_app.serverless_mc.cli session-info
python -m helper_app.serverless_mc.cli publish-session
python -m helper_app.serverless_mc.cli upload-world
python -m helper_app.serverless_mc.cli download-world
python -m helper_app.serverless_mc.cli validate-cache
python -m helper_app.serverless_mc.cli set-host Bob
python -m helper_app.serverless_mc.cli auth-mode offline
python -m helper_app.serverless_mc.cli configure-rcon
python -m helper_app.serverless_mc.cli launch-server --memory 2G
python -m helper_app.serverless_mc.cli server-status
python -m helper_app.serverless_mc.cli save-world
python -m helper_app.serverless_mc.cli sync-world
python -m helper_app.serverless_mc.cli stop-server
python -m helper_app.serverless_mc.cli manual-switch --to-config .serverless-mc/target.json --launch
```

Use `auth-mode offline` only for trusted private testing with clients that cannot verify Microsoft/Mojang sessions.
Use `configure-rcon` before launching the server if you want graceful save/stop commands.

## Network Scanner

Scan the local network for running helpers:

```powershell
python check_standbys.py
python check_standbys.py --config .serverless-mc/viperf29.json
python check_standbys.py --extra-ips 10.136.97.35
```

## Cloud Relay

Start a relay server:

```powershell
python -m cloud_relay.server init
python -m cloud_relay.server
```

Configure each helper to use it:

```powershell
python -m helper_app.serverless_mc.cli configure-relay --url http://<relay-ip>:9000 --api-key <key>
```

Disable relay storage and return to `.local-cloud/`:

```powershell
python -m helper_app.serverless_mc.cli configure-relay --disable
```

## WSL Address Note

If the host is running inside WSL2, the Minecraft server binds to `0.0.0.0:25565` inside the VM. The helper detects the WSL VM internal IP (`172.x.x.x`) and automatically publishes it. 

> **Note:** The helper defaults to the WSL VM IP because Windows' built-in `localhost` forwarding can be flaky across reboots. If you specifically need `127.0.0.1` advertised, set `SERVERLESS_MC_LOCALHOST_FWD=1`.

## Multi-Laptop Setup

1. Clone this repo on both machines.
2. Place the Fabric server jar in `server/server.jar`.
3. Run `python -m helper_app.serverless_mc.cli init --player-name <name> --world-id prototype-world` on each.
4. Share the `.local-cloud/` folder between machines, or configure both helpers to use the Cloud Relay Server.
5. Copy `serverless_mc-1.0.0.jar` from `serverless_mc_mod/build/libs/` into each client's Minecraft `mods/` folder (along with Fabric API).
6. Run `serve --allow-host-promotion` on both — the helper on the standby machine will auto-promote if the active host goes down.

## Same-Laptop Testing

If a second laptop is not available, use [same-laptop-test.md](docs/same-laptop-test.md). It uses `--config` to simulate a second player profile against the same local cloud snapshot.

For the manual host-switch milestone, use [manual-host-switch-simulation.md](docs/manual-host-switch-simulation.md).
