# Same-Laptop Multi-Player Test

This is a phase-one substitute for a second laptop. It does not prove VPN behavior, but it does prove that:

- The server accepts multiple offline-mode usernames.
- The helper can keep separate player configs.
- The world can be safely flushed and uploaded.
- A second profile can download and validate the latest snapshot.

## Setup

Use the bundled Python on this machine:

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

The main host config is:

```powershell
D:\minecrafrserver\.serverless-mc\config.json
```

Create a second local profile:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json init --player-name Bob --world-id prototype-world
```

Edit `.serverless-mc\bob.json` so:

- `cloud_root` matches the host config: `D:\minecrafrserver\.local-cloud`
- `local_world_path` points at a separate cache folder, for example `D:\minecrafrserver\.profiles\bob\world`
- `cache_dir` points at `D:\minecrafrserver\.profiles\bob\cache`
- `server_dir` can stay separate unless Bob is being tested as host

## Current Host Test

With the real server already running, flush and upload the host world:

```powershell
& $PY -m helper_app.serverless_mc.cli sync-world
```

Download and validate the same snapshot as Bob:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json download-world
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json validate-cache
```

Launch a second TLauncher/Minecraft instance with username `Bob` and join:

```text
127.0.0.1:25565
```

## What WSL Can Test Later

WSL is optional. Do not use it for the Minecraft GUI test. Later it can be useful for checking whether a Linux helper can read the same `.local-cloud` snapshot metadata and validate archive formats.

Suggested WSL-only check when requested:

```bash
cd /mnt/d/minecrafrserver
python3 -m helper_app.serverless_mc.cli --config .serverless-mc/bob-linux.json show-config
```

