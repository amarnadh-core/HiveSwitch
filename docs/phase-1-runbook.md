# Phase 1 Runbook

## Goal

Prove that two players can connect to the same vanilla world, manually sync that world, and manually switch the active host.

## Prerequisites

- Python 3.11 or newer
- Java runtime compatible with the chosen Minecraft server jar
- Vanilla Minecraft server jar
- ZeroTier installed if testing across different networks
- A shared `cloud_root` folder, or an object-storage mount that behaves like a folder

## Host Setup

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY -m helper_app.serverless_mc.cli init --player-name Alice --world-id demo-world
& $PY -m helper_app.serverless_mc.cli auth-mode offline
& $PY -m helper_app.serverless_mc.cli configure-rcon
& $PY -m helper_app.serverless_mc.cli join-vpn
& $PY -m helper_app.serverless_mc.cli upload-world
& $PY -m helper_app.serverless_mc.cli launch-server --memory 2G
& $PY -m helper_app.serverless_mc.cli serve
```

The host connects their Minecraft client to `127.0.0.1:25565`.

`auth-mode offline` is for trusted private testing with TLauncher/offline clients. For Microsoft/Mojang-authenticated clients, use `auth-mode online`.

To check or stop a helper-managed server:

```powershell
& $PY -m helper_app.serverless_mc.cli server-status
& $PY -m helper_app.serverless_mc.cli save-world
& $PY -m helper_app.serverless_mc.cli sync-world
& $PY -m helper_app.serverless_mc.cli stop-server
```

`sync-world` sends `save-all flush` through RCON, waits for Minecraft to flush the world, then uploads a fresh snapshot.
`configure-rcon` generates a random RCON password unless you pass `--password`.

## Joining Player Setup

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
& $PY -m helper_app.serverless_mc.cli init --player-name Bob --world-id demo-world
& $PY -m helper_app.serverless_mc.cli join-vpn
& $PY -m helper_app.serverless_mc.cli download-world
& $PY -m helper_app.serverless_mc.cli validate-cache
& $PY -m helper_app.serverless_mc.cli serve
```

The joining player connects to the host's ZeroTier IP on port `25565`.

## Manual Host Switch

On the current host:

```powershell
& $PY -m helper_app.serverless_mc.cli sync-world
& $PY -m helper_app.serverless_mc.cli set-host Bob
```

On the new host:

```powershell
& $PY -m helper_app.serverless_mc.cli download-world
& $PY -m helper_app.serverless_mc.cli validate-cache
& $PY -m helper_app.serverless_mc.cli set-host Bob
& $PY -m helper_app.serverless_mc.cli launch-server --memory 2G
```

Players reconnect to the new host's ZeroTier IP.

## Prototype API

- `GET /session`
- `GET /host-score`
- `GET /migration-state`
- `GET /latest-snapshot`
- `GET /reconnect`
- `POST /request-host`
- `POST /migration-ack`
- `POST /player-ready`
- `POST /portal-entered`

All responses include a phase-one protocol version where relevant.
