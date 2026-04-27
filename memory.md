# Project Memory

## Current Goal

Build phase one of a hybrid "serverless" Minecraft multiplayer prototype where one player's machine hosts a hidden vanilla dedicated server, world data can be manually uploaded/downloaded, and host control can be manually switched before later automation phases.

## Workspace

- Root: `D:\minecrafrserver`
- Spec: `D:\minecrafrserver\minecraft_serverless_server_architecture_spec_v_1 (2).md`
- Helper app: `D:\minecrafrserver\helper_app\serverless_mc`
- Docs: `D:\minecrafrserver\README.md`, `D:\minecrafrserver\docs\phase-1-runbook.md`
- Active config: `D:\minecrafrserver\.serverless-mc\config.json`
- Real server folder: `D:\minecrafrserver\server`
- Real server jar: `D:\minecrafrserver\server\server.jar`
- Real world folder: `D:\minecrafrserver\server\world`

## Runtime Notes

- System Java is installed and works: JDK 21.
- The server jar is compatible with JDK 21 and was manually verified by the user.
- System `python` is not on PATH. Use bundled Python:

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

## Implemented So Far

- Python helper CLI with no third-party dependencies.
- Config loading/saving.
- Local filesystem-backed cloud storage under `.local-cloud`.
- World snapshot upload/download using `tar.gz`.
- SHA-256 manifest validation.
- Hidden-ish Minecraft server launch.
- Helper API/reconnect page.
- Manual host setting.
- Helper-managed server PID tracking.
- `server-status` and `stop-server` commands.
- `auth-mode online|offline` command added for switching server authentication mode.
- RCON client and commands added: `configure-rcon`, `server-command`, `save-world`, `sync-world`. `configure-rcon` generates a random RCON password unless `--password` is supplied.
- CLI now supports `--config <path>` for separate local player profiles.
- CLI now supports `create-profile <name>` for creating profile configs without hand-editing JSON.
- CLI now supports `manual-switch` to sync/stop the source host, download/validate on the target profile, set target host, and optionally launch the target server.
- CLI now supports `connect-info`, and configs include `server_host`, `server_port`, and `server_label`.
- Shared session metadata now exists at `.local-cloud/worlds/prototype-world/session.json`.
- CLI now supports `publish-session` and `session-info`.
- Web UI updated with automated JS polling for `http://127.0.0.1:8765/session` indicating host migrations seamlessly (Phase 2.1).
- Fabric 1.20.1 Mod Created using Mixins into `Screen.class` enabling true zero-touch in-game auto reconnects upon seeing the "Migrating..." state (Phase 2.2).
- Migrated storage layer from saving massive monolithic `world.tar.gz` blobs to Delta Region Syncing using file diffing and granular uploads/downloads (Phase 2.3).
- Leader Election & Heartbeats: Background orchestrator thread in `serve` monitors `session.json` heartbeat timestamps. Standby helpers detect host death after 3 missed heartbeats (~15s) and race for an atomic `election.lock` file. Winner publishes `migrating` state and optionally auto-promotes with `--allow-host-promotion` (Phase 2.4).
- Cross-platform process management: `minecraft.py` uses `creationflags` on Windows, `os.setsid` on Linux/WSL. `cli.py` uses `tasklist`/`taskkill` on Windows, `os.kill` on Linux. PID file now stores JSON with `pid`, `platform`, and `host_player` fields (with legacy integer fallback).

## Current Server State

- Helper config points at the real server folder and jar.
- `upload-world` succeeded for the real server world using delta region sync logic.
- `validate-cache` passed for the uploaded snapshot.
- Helper-managed launch succeeded when run with elevated permissions.
- Current helper-managed server PID is `3648`.
- Local connection check to `127.0.0.1:25565` succeeded after relaunch.
- RCON is enabled on port `25575` with a generated password stored in `server.properties`.
- `save-world` works through RCON.
- `sync-world` works through RCON and uploaded snapshot `20260419T175529Z`.
- `validate-cache` passed against the latest uploaded snapshot.
- Server log confirms offline mode is active.
- Same-laptop profile support was tested.
- Bob profile exists at `D:\minecrafrserver\.serverless-mc\bob.json`.
- Bob profile downloaded snapshot `20260419T183136Z` to `D:\minecrafrserver\.profiles\bob\world`.
- Bob profile `validate-cache` passed.
- Bob profile currently has `host_player` set to `PlayerOne`.
- Bob profile `server_dir` is `D:\minecrafrserver\.profiles\bob`, so Bob's downloaded world is also Bob's server world.
- Manual host-switch simulation guide exists at `D:\minecrafrserver\docs\manual-host-switch-simulation.md`.
- Bob alternate host server folder is prepared at `D:\minecrafrserver\.profiles\bob`.
- Bob alternate host server port is `25566`.
- Bob alternate host RCON port is `25576`.
- Bob alternate host is set to offline auth for TLauncher compatibility.
- Bob alternate host EULA has not been accepted yet; `D:\minecrafrserver\.profiles\bob\eula.txt` does not exist.
- User attempted host switch with `.serverless-mc\viperf28.json`, which did not exist. Added `create-profile` command and created `D:\minecrafrserver\.serverless-mc\viperf28.json`.
- Viperf28 profile root is `D:\minecrafrserver\.profiles\viperf28`.
- Viperf28 profile server port is `25566`, RCON port is `25576`, offline auth is enabled.
- Viperf28 downloaded snapshot `20260419T211227Z` and `validate-cache` passed.
- Viperf28 EULA has not been accepted yet; user should run `configure-server --port 25566 --accept-eula` with the viperf28 config before launch.
- User later accepted EULA for viperf28 and confirmed host switch worked; viperf28 server currently reported as PID `21708` and port `25566` is reachable.
- Created `D:\minecrafrserver\.serverless-mc\viperf29.json` for the other TLauncher username.
- Viperf29 profile root is `D:\minecrafrserver\.profiles\viperf29`, server port `25565`, RCON port `25575`, offline auth enabled.
- Viperf29 EULA has not been accepted yet for its generated profile folder.
- `manual-switch` dry run from viperf28 to viperf29 passed with `--skip-source-sync --skip-source-stop`.
- User ran real `manual-switch` from viperf28 to viperf29 and initially hit region hash mismatches because the old uploader built the manifest before archiving while the server world could still change.
- Fixed uploader so each manifest hash is computed from the exact bytes written into `world.tar.gz`.
- Fixed `manual-switch` order so the default flow stops the source server first, then uploads the stopped world as the handoff snapshot.
- Replaced bad snapshot `20260419T214829Z` with valid snapshot `20260419T215008Z`.
- Viperf29 downloaded and validated snapshot `20260419T215008Z`.
- Viperf29 is now the active simulated host on port `25565`, PID `12668`; RCON is live on port `25575`.
- Viperf28 connect info: label `viperf28 host`, address `127.0.0.1:25566`.
- Viperf29 connect info: label `viperf29 host`, address `127.0.0.1:25565`.
- Published shared session metadata says current host is `viperf29`, server entry `viperf29 host`, address `127.0.0.1:25565`.
- Web `HelperState.session_payload()` was checked from the viperf28 profile and correctly returned host `viperf29`, address `127.0.0.1:25565`.
- Delta syncing migrated `world.tar.gz` to individual raw file diffing, drastically dropping world upload times.

## Current Issue

Resolved. After WSL promotion, the system defaults to advertising the WSL VM internal IP (e.g., `172.x.x.x`). 
This is because WSL2's localhost forwarding (`127.0.0.1` bridge) can be flaky across reboots. The WSL VM IP is reachable from the Windows host via the virtual network bridge. If the user explicitly wants `127.0.0.1` advertised, they must pass `SERVERLESS_MC_LOCALHOST_FWD=1`.

## Useful Commands

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

& $PY -m helper_app.serverless_mc.cli show-config
& $PY -m helper_app.serverless_mc.cli auth-mode offline
& $PY -m helper_app.serverless_mc.cli configure-rcon
& $PY -m helper_app.serverless_mc.cli upload-world
& $PY -m helper_app.serverless_mc.cli sync-world
& $PY -m helper_app.serverless_mc.cli validate-cache
& $PY -m helper_app.serverless_mc.cli launch-server --memory 2G
& $PY -m helper_app.serverless_mc.cli server-status
& $PY -m helper_app.serverless_mc.cli stop-server
& $PY -m helper_app.serverless_mc.cli serve --allow-host-promotion
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json download-world
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json validate-cache
```

Launching the Minecraft server through the helper may require elevated permissions because the desktop sandbox can block Java network/socket calls.

## WSL Notes

- WSL Ubuntu has `openjdk-21-jre-headless` (21.0.10) and Python 3.12.3 installed.
- WSL accesses the Windows filesystem via `/mnt/d/minecrafrserver`.
- WSL and Windows share the same `.local-cloud` folder natively through the mount.
- PID file includes `platform` field to prevent cross-platform kill conflicts.
- WSL standby profile: `.serverless-mc/wsl_standby.json`
  - Profile dir: `.profiles/wsl_standby/`
  - Helper port: `8766` (Windows host uses `8765`)
  - Server port: `25565`
  - RCON port: `25575`, password: `smc-wsl-standby-rcon`
  - Offline auth + EULA accepted
  - `server.properties` binds to `0.0.0.0`
- WSL2 networking: NAT-based.
  - The WSL VM internal IP (`172.x.x.x`) is reachable from the Windows host via the virtual vEthernet switch.
  - The helper defaults to advertising this WSL VM IP to guarantee connectivity without relying on flaky port proxies.
  - If `SERVERLESS_MC_LOCALHOST_FWD=1` is set, it will verify and advertise `127.0.0.1:25565` instead.
- WSL CLI usage from Windows terminal:
  ```
  wsl -- bash -c "cd /mnt/d/minecrafrserver && SERVERLESS_MC_CONFIG=/mnt/d/minecrafrserver/.serverless-mc/wsl_standby.json python3 -m helper_app.serverless_mc.cli <command>"
  ```
- WSL CLI usage from within WSL:
  ```
  cd /mnt/d/minecrafrserver
  export SERVERLESS_MC_CONFIG=/mnt/d/minecrafrserver/.serverless-mc/wsl_standby.json
  python3 -m helper_app.serverless_mc.cli <command>
  ```

### WSL Reconnect Verification

After WSL promotion, run in WSL:

```
cd /mnt/d/minecrafrserver
export SERVERLESS_MC_CONFIG=/mnt/d/minecrafrserver/.serverless-mc/wsl_standby.json
python3 -m helper_app.serverless_mc.cli publish-session
python3 -m helper_app.serverless_mc.cli session-info
```

`session.json` should show `server_address=<wsl_ip>:25565` (not `127.0.0.1:25565`).

## Phase 3 Test Results

- WSL standby (`wsl_standby`) successfully detected Windows host death via heartbeat timeout.
- Atomic election lock acquired from Linux filesystem.
- Delta download (20 files) completed and passed SHA-256 validation.
- Java server launched from WSL as pid=4240 (platform: linux).
- Session metadata automatically updated: `current_host: wsl_standby`, `state: active`.
- WSL heartbeat thread is refreshing `session.json` continuously.
- Full Windows→Linux automatic cross-platform host migration proven.

## Next Steps

1. Test Minecraft client reconnecting to the WSL-hosted server at `localhost:25565`.
2. Test reverse failover: WSL host active → Windows standby takes over.
3. Later, set up ZeroTier and test a second laptop joining through the host's ZeroTier IP.
4. For multi-machine support, add `netsh interface portproxy` automation so remote players can reach the WSL server.
