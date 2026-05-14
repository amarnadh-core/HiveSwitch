# Project Memory

## Current Goal

Build and harden the "serverless" Minecraft multiplayer architecture. A Cloud Relay Server is next — an HTTP intermediary that replaces the shared `.local-cloud` folder so helpers on different networks can upload/download snapshots without needing Windows file sharing.

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

## Current Milestone

**Phase 4 in progress: Cloud Relay Server.**

All local-machine and cross-platform (Windows ↔ WSL) features are stable. Two-laptop testing over Wi-Fi has been validated. The system is blocked on the `.local-cloud` shared folder requirement — building an HTTP Cloud Relay removes this dependency.

## Implemented Features

Relay update: the HTTP Cloud Relay is now implemented locally and has passed integration tests. The remaining proof step is a real two-laptop handoff using the relay instead of a shared `.local-cloud` folder.

- Python helper CLI with no third-party dependencies.
- Config loading/saving.
- Local filesystem-backed cloud storage under `.local-cloud` (full + delta file snapshots).
- SHA-256 manifest validation with repair and rollback recovery.
- Hidden Minecraft server launch with PID tracking (JSON format: `pid`, `platform`, `host_player`).
- Helper HTTP API (`/session`, `/reconnect`, `/host-score`, etc.) bound to `0.0.0.0`.
- Manual host setting via `set-host`.
- `server-status` and `stop-server` commands.
- `auth-mode online|offline` for switching server authentication mode.
- RCON client: `configure-rcon`, `server-command`, `save-world`, `sync-world`.
- `--config <path>` for separate local player profiles.
- `create-profile <name>` for creating profile configs without hand-editing JSON.
- `manual-switch` to sync/stop source, download/validate on target, set host, and optionally launch.
- `connect-info`, and configs include `server_host`, `server_port`, `server_label`.
- Shared session metadata at `.local-cloud/worlds/{world_id}/session.json`.
- `publish-session` and `session-info` commands.
- Web reconnect page with automated JS polling for host migrations (Phase 2.1).
- Fabric 1.20.1 Mod with Mixins for true zero-touch in-game auto reconnects (Phase 2.2).
- Delta Region Syncing — file-level diffing with granular uploads/downloads (Phase 2.3).
- Leader Election & Heartbeats — background orchestrator with atomic `election.lock` (Phase 2.4).
- Cross-platform process management: `creationflags` on Windows, `os.setsid` on Linux/WSL.
- ZeroTier join hook (`join-vpn`).
- Cloud Relay Server (`cloud_relay`) with HTTP session/latest/snapshot/election endpoints.
- Helper storage factory switches between local `.local-cloud` and `RemoteCloudStorage` when `relay_url` is configured.
- `configure-relay` CLI command enables/disables relay storage in helper configs.

## 2026-05-13 Code Audit / Cleanup Pass

- `sync-world` now uses the staging snapshot path instead of uploading the live world folder directly.
- `manual-switch` now preserves `handoff_snapshot` when publishing cross-platform or standby-promotion migration state.
- `publish-session` now reports the actual advertised Minecraft address, which matters for WSL hosts.
- Storage lock metadata now records platform/host/nonce and avoids using local PID checks to invalidate foreign Windows/WSL locks.
- Snapshot IDs now include microseconds and retry suffixes to avoid same-second collisions.
- Upload permission during `migrating`/`promoting` is now restricted to the active handoff participants.
- Fabric template remnants were removed from source: `modid` project naming, example client classes, example mixin config, and mismatched icon path.
- Fabric session parsing now uses Gson instead of brittle string search.

## 2026-05-14 Cloud Relay Pass

- Added/finished HTTP-backed `RemoteCloudStorage` integration for helper commands and auto-promotion.
- Local and remote storage now expose a common election API (`try_election`, `release_election`), removing local-only `election.lock` access from the web orchestrator.
- Relay server now enforces configured upload-size and snapshot-retention limits from `cloud_relay.json`.
- Relay storage now validates `world_id` and `snapshot_id` path components to prevent traversal through encoded URL paths.
- Remote uploads now hash the bytes actually sent to the relay, matching the local snapshot safety model.
- Added `cloud-relay` script entry and helper `configure-relay` command.
- `test_relay_integration.py` is self-contained: starts a temporary relay, round-trips session data, uploads full + delta snapshots, downloads/validates, and tests election locking.
- Verification passed with bundled Python: `compileall`, CLI help, relay help, and all 7 relay integration tests.

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

## Multi-Laptop Test Results (2026-04-27 to 2026-05-08)

- Two-laptop testing over home Wi-Fi validated (192.168.1.x subnet).
- ZeroTier network `2873fd00f2f6a6be` joined on both laptops.
- Helper HTTP API re-bound from `127.0.0.1` to `0.0.0.0` so network peers can query `/session`.
- Network scanner (`check_standbys.py`) confirmed cross-laptop helper discovery.
- Blocked on `.local-cloud` shared folder — Windows File Sharing works but is fragile; Cloud Relay Server is the permanent fix.

## Historical Notes

These are older milestones that have been superseded but kept for reference.

- **tar.gz era**: The original storage layer uploaded/downloaded the entire world as a single `world.tar.gz` blob. This was replaced by per-file full + delta snapshots in Phase 2.3.
- **Manifest hashing bug**: The original uploader hashed files before archiving, causing mismatches when Minecraft wrote region files mid-copy. Fixed by hashing the bytes actually written to the snapshot.
- **Bob/viperf28 profiles**: Early same-laptop test profiles created during Phase 1 development. Bob used port `25566`, viperf28 also used `25566`. These are still present in `.serverless-mc/` but are not actively used.
- **connect_address rename**: `connect_address` was renamed to `config_address` during cleanup to distinguish between configuration values and actual advertised network addresses.

## Next Steps

1. Run real two-laptop handoff over the Cloud Relay instead of shared filesystem.
2. Confirm auto-promotion over relay with both helpers using `serve --allow-host-promotion`.
3. Move process helpers (`_is_pid_alive`, `_force_kill_local_server`) out of `web.py` into `minecraft.py` or `process.py`.
