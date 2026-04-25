# Serverless Minecraft Prototype

This workspace contains the phase-one helper prototype for the architecture described in `minecraft_serverless_server_architecture_spec_v_1 (2).md`.

Phase one is intentionally simple:

- Manual host selection
- ZeroTier join hook
- Basic world upload/download through a filesystem-backed "cloud" folder
- SHA-256 manifest validation
- Hidden dedicated server launch
- Localhost HTTP helper API
- Simple reconnect screen

The storage layer uses `.local-cloud/` for now. Put that folder on a shared path, sync tool, or mounted object-storage adapter when testing across machines.

## Quick Start

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY -m helper_app.serverless_mc.cli init --player-name Alice --world-id demo-world
& $PY -m helper_app.serverless_mc.cli show-config
```

If you install Python 3.11 or newer and add it to PATH, you can use `python` instead of `& $PY`.

Edit `.serverless-mc/config.json` and set:

- `local_world_path` to the world folder you want to share
- `server_jar` to a vanilla Minecraft server jar
- `zerotier_network_id` to your network ID, if ZeroTier is installed
- `cloud_root` to a shared folder both players can access

Start the helper API:

```powershell
& $PY -m helper_app.serverless_mc.cli serve
```

Open `http://127.0.0.1:8765/reconnect` for the prototype reconnect screen.

## Common Commands

```powershell
& $PY -m helper_app.serverless_mc.cli join-vpn
& $PY -m helper_app.serverless_mc.cli connect-info
& $PY -m helper_app.serverless_mc.cli session-info
& $PY -m helper_app.serverless_mc.cli publish-session
& $PY -m helper_app.serverless_mc.cli upload-world
& $PY -m helper_app.serverless_mc.cli download-world
& $PY -m helper_app.serverless_mc.cli validate-cache
& $PY -m helper_app.serverless_mc.cli set-host Bob
& $PY -m helper_app.serverless_mc.cli auth-mode offline
& $PY -m helper_app.serverless_mc.cli configure-rcon
& $PY -m helper_app.serverless_mc.cli launch-server --memory 2G
& $PY -m helper_app.serverless_mc.cli server-status
& $PY -m helper_app.serverless_mc.cli save-world
& $PY -m helper_app.serverless_mc.cli sync-world
& $PY -m helper_app.serverless_mc.cli stop-server
```

Use `auth-mode offline` only for trusted private testing with clients that cannot verify Microsoft/Mojang sessions. Use `auth-mode online` for normal authenticated vanilla accounts.
Use `configure-rcon` before launching the server if you want graceful save/stop commands. It generates a random RCON password unless you pass `--password`.
Use `session-info` to see the current shared host address published in `.local-cloud`.

## WSL Address Note

If the host is running inside WSL2, the Minecraft server binds to `0.0.0.0:25565` inside the VM. WSL2's built-in **localhost forwarding** (enabled by default) bridges `localhost:25565` from Windows into the VM transparently. The session publishes `127.0.0.1:25565` and Windows Minecraft clients connect to `localhost:25565`.

> **Note:** The WSL VM's internal IP (`172.x.x.x`) is *not* reachable from Windows due to WSL2 NAT isolation. Do not use it as the connect address.

## Phase-One Testing Flow

1. Both players join the same ZeroTier network.
2. Player A sets themselves as host and uploads the world.
3. Player A launches the hidden server.
4. Player B downloads the latest world snapshot for validation/cache.
5. Player B connects Minecraft to Player A's VPN IP on port `25565`.
6. To manually switch hosts, Player A uploads the latest world, Player B downloads it, both configs run `set-host Bob`, and Player B launches the server.

This proves the phase-one exit criteria. Phase 2 features such as **Reconnect Automation (Fabric Mod)** and **Dirty Region Deltas (Object storage)** are implemented and available for testing. Next is automatic migration and leases.

## Same-Laptop Testing

If a second laptop is not available, use [same-laptop-test.md](docs/same-laptop-test.md). It uses `--config` to simulate a second player profile against the same local cloud snapshot.

For the manual host-switch milestone, use [manual-host-switch-simulation.md](docs/manual-host-switch-simulation.md). It runs the simulated new host on port `25566`.
The helper also includes `manual-switch` to bundle the phase-one handoff steps once both profiles are prepared.
