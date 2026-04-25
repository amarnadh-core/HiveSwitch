# Manual Host Switch Simulation

This simulates phase-one host switching on one laptop. Use one profile name consistently. If your second Minecraft username is `viperf28`, use `.serverless-mc\viperf28.json` everywhere. If you created `bob.json`, use `.serverless-mc\bob.json` everywhere.

It uses:

- `PlayerOne` profile as current host on port `25565`
- `Bob` profile as next host on port `25566`
- The same `.local-cloud` snapshot folder

This does not prove VPN behavior, but it proves the manual world handoff flow.

## Runtime

```powershell
$PY = "C:\Users\LENOVO\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
```

## Step 1: Current Host Flushes And Uploads

Run this while PlayerOne's server is running:

```powershell
& $PY -m helper_app.serverless_mc.cli sync-world
```

## Step 2: Stop Current Host

```powershell
& $PY -m helper_app.serverless_mc.cli stop-server
```

If graceful stop fails:

```powershell
& $PY -m helper_app.serverless_mc.cli stop-server --force
```

## Step 3: Bob Downloads Latest World

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json download-world
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json validate-cache
```

For a different username, create a matching profile first:

```powershell
& $PY -m helper_app.serverless_mc.cli create-profile viperf28 --output .serverless-mc\viperf28.json --from-config .serverless-mc\bob.json --host-player PlayerOne
```

## Step 4: Prepare Bob's Server Folder

Bob's same-laptop test server uses port `25566`.

Only run `--accept-eula` if you agree to the Minecraft EULA for this generated server folder:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json configure-server --port 25566 --accept-eula
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json auth-mode offline
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json configure-rcon --port 25576
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json set-host Bob
```

## Step 5: Launch Bob As Host

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json launch-server --memory 2G
```

Join Minecraft/TLauncher at:

```text
127.0.0.1:25566
```

## Step 6: Sync From Bob

When Bob is acting as host:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\bob.json sync-world
```

## One-Command Workflow

After both profiles are prepared and the target profile has accepted the EULA, use `manual-switch`.

Switch from the currently running `viperf29` host to `viperf28` on port `25566`:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf29.json manual-switch --to-config .serverless-mc\viperf28.json --to-host viperf28 --port 25566 --launch
```

Switch from the currently running `viperf28` host back to `viperf29` on port `25565`:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf28.json manual-switch --to-config .serverless-mc\viperf29.json --to-host viperf29 --port 25565 --launch
```

For a safe dry run that does not stop the current server or launch the target server:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf28.json manual-switch --to-config .serverless-mc\viperf29.json --to-host viperf29 --skip-source-sync --skip-source-stop
```

To print the correct Minecraft server list entry for any profile:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf29.json connect-info
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf28.json connect-info
```

To print the current shared session host, which is what the reconnect page uses:

```powershell
& $PY -m helper_app.serverless_mc.cli --config .serverless-mc\viperf28.json session-info
```
