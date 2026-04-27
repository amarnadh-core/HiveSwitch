from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import HelperConfig, current_config_path, save_config
from .storage import LocalCloudStorage


class HelperState:
    def __init__(self, config: HelperConfig):
        self.config = config
        self.migration_state = "idle"
        self.requested_host = ""
        self.ready_players: set[str] = set()

    @property
    def is_host(self) -> bool:
        return self.config.host_player == self.config.player_name

    def session_payload(self) -> dict[str, Any]:
        storage = LocalCloudStorage(self.config.cloud, self.config.world_id)
        try:
            shared = storage.session()
            host_player = shared.current_host
            server_address = shared.server_address
            server_label = shared.server_label
            state = shared.state
            
            now = datetime.now(timezone.utc)
            updated_at = datetime.fromisoformat(shared.updated_at)
            age = (now - updated_at).total_seconds()
            
            # Synthesize early freeze if heartbeat is stale (>12s) and we
            # are NOT the host.  The 5-second heartbeat can legitimately
            # age up to ~10s between refreshes; 12s avoids false positives.
            # The host's own helper must never fake-migrate itself.
            if (age > 12.0
                    and state == "active"
                    and host_player != self.config.player_name):
                state = "migrating"

            # Map internal 'promoting' state to 'migrating' for the mod
            if state == "promoting":
                state = "migrating"

        except FileNotFoundError:
            host_player = self.config.host_player
            server_address = self.config.connect_address
            server_label = self.config.server_label
            state = "local"
        return {
            "version": 1,
            "session_id": self.config.session_id,
            "world_id": self.config.world_id,
            "player_name": self.config.player_name,
            "host_player": host_player,
            "is_host": host_player == self.config.player_name,
            "server_address": server_address,
            "server_label": server_label,
            "state": state,
        }


def make_handler(state: HelperState):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/session":
                self._json(200, state.session_payload())
            elif path == "/host-score":
                self._json(200, {"version": 1, "session_id": state.config.session_id, "quality": "Manual", "score": None})
            elif path == "/migration-state":
                self._json(200, {"version": 1, "session_id": state.config.session_id, "state": state.migration_state})
            elif path == "/reconnect":
                self._reconnect_page()
            elif path == "/latest-snapshot":
                storage = LocalCloudStorage(state.config.cloud, state.config.world_id)
                try:
                    self._json(200, storage.latest().__dict__)
                except FileNotFoundError as exc:
                    self._json(404, {"version": 1, "error": str(exc)})
            else:
                self._json(404, {"version": 1, "error": "not_found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = self._body()
            if path == "/request-host":
                state.requested_host = body.get("player_name", state.config.player_name)
                state.migration_state = "manual_host_requested"
                self._json(202, {"version": 1, "session_id": state.config.session_id, "requested_host": state.requested_host})
            elif path == "/migration-ack":
                state.migration_state = body.get("state", "acknowledged")
                self._json(200, {"version": 1, "session_id": state.config.session_id, "state": state.migration_state})
            elif path == "/player-ready":
                player = body.get("player_name", state.config.player_name)
                state.ready_players.add(player)
                self._json(200, {"version": 1, "session_id": state.config.session_id, "ready_players": sorted(state.ready_players)})
            elif path == "/portal-entered":
                self._json(202, {"version": 1, "session_id": state.config.session_id, "status": "portal preload is phase four"})
            else:
                self._json(404, {"version": 1, "error": "not_found"})

        def _reconnect_page(self) -> None:
            session = state.session_payload()
            html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Minecraft Helper - Reconnect</title>
  <style>
    body {{ margin: 0; font-family: Segoe UI, Arial, sans-serif; background: #101418; color: #f1f5f9; }}
    main {{ max-width: 720px; margin: 12vh auto; padding: 32px; }}
    h1 {{ font-size: 32px; margin: 0 0 12px; }}
    p {{ color: #b8c2cc; line-height: 1.5; }}
    code {{ background: #1d2630; padding: 3px 6px; border-radius: 4px; border: 1px solid #2d3748; cursor: pointer; }}
    code:hover {{ background: #2d3748; }}
    .status-badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 13px; font-weight: bold; margin-bottom: 20px; text-transform: uppercase; }}
    .status-active {{ background: #2f8f68; color: white; }}
    .status-migrating {{ background: #f59e0b; color: white; animation: pulse 2s infinite; }}
    @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.5; }} 100% {{ opacity: 1; }} }}
  </style>
  <script>
    let currentHost = "{session["host_player"]}";
    let currentAddress = "{session["server_address"]}";
    
    function copyAddress() {{
      const address = document.getElementById('server-address').innerText;
      navigator.clipboard.writeText(address).then(() => {{
        const btn = document.getElementById('server-address');
        const old = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => btn.innerText = address, 1000);
      }});
    }}

    setInterval(async () => {{
      try {{
        const res = await fetch('/session');
        if (!res.ok) return;
        const data = await res.json();
        
        let hostChanged = false;
        
        if (data.server_address !== currentAddress || data.host_player !== currentHost) {{
          document.getElementById('host-player').innerText = data.host_player;
          document.getElementById('server-label').innerText = data.server_label;
          document.getElementById('server-address').innerText = data.server_address;
          currentHost = data.host_player;
          currentAddress = data.server_address;
          hostChanged = true;
        }}
        
        const badge = document.getElementById('status-badge');
        if (data.state === 'migrating' || data.state === 'idle') {{
          badge.className = 'status-badge status-migrating';
          badge.innerText = 'Host Migrating...';
          document.title = 'Migrating...';
        }} else {{
          badge.className = 'status-badge status-active';
          badge.innerText = 'Server Active';
          document.title = 'Ready to Join';
          
          if (hostChanged) {{
            const audio = new Audio('data:audio/wav;base64,UklGRl9vT19XQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YU');
            // Simplified silent dummy audio to beep, or could just flash title
            // A more practical approach is flashing the title
            let flasher = setInterval(() => {{
                document.title = document.title === '🚀 NEW HOST READY' ? 'Ready to Join' : '🚀 NEW HOST READY';
            }}, 500);
            setTimeout(() => clearInterval(flasher), 5000);
          }}
        }}

      }} catch (e) {{
        console.error("Polling error", e);
      }}
    }}, 2000);
  </script>
</head>
<body>
  <main>
    <h1>Host Connection Details</h1>
    <div id="status-badge" class="status-badge status-active">Server Active</div>
    <p>The current host is <strong id="host-player">{session["host_player"]}</strong>.</p>
    <p>Server entry: <strong id="server-label">{session["server_label"]}</strong></p>
    <p>Connect Minecraft to <code id="server-address" onclick="copyAddress()" title="Click to copy">{session["server_address"]}</code>.</p>
  </main>
</body>
</html>"""
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


def _wait_for_rcon(timeout: float = 60.0) -> bool:
    """Wait until RCON is connectable. Returns True if ready."""
    from .cli import _rcon_client_from_config
    start = time.time()
    while time.time() - start < timeout:
        try:
            with _rcon_client_from_config() as rcon:
                rcon.command("list")
            return True
        except Exception:
            time.sleep(2)
    return False


def _is_pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        res = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                             check=False, capture_output=True, text=True)
        return str(pid) in res.stdout
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _force_kill_local_server(config: HelperConfig, only_if_owned: bool = True) -> None:
    """Kill local Java server by PID. Non-blocking, no RCON.
    
    If only_if_owned=True (default), only kills if PID owner matches
    this helper's player_name or the process is provably stale.
    """
    from .minecraft import read_pid, pid_file
    pid_data = read_pid(config.server_workdir)
    if pid_data is None:
        return
    pid = pid_data["pid"]
    platform = pid_data.get("platform", "legacy")
    if platform != "legacy" and platform != sys.platform:
        return
    if not _is_pid_alive(pid):
        try:
            pid_file(config.server_workdir).unlink()
        except OSError:
            pass
        return
    # Fix 3: Conditional kill — only if we own it or stale
    owner = pid_data.get("host_player", "")
    if only_if_owned and owner and owner != config.player_name:
        print(f"[CLEANUP] Server pid={pid} owned by '{owner}', not killing.")
        return
    print(f"[CLEANUP] Force-killing server pid={pid} (owner='{owner}')...")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       check=False, capture_output=True)
    else:
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for _ in range(5):
        if not _is_pid_alive(pid):
            break
        time.sleep(0.5)
    try:
        pid_file(config.server_workdir).unlink()
    except OSError:
        pass


def _do_promotion(config: HelperConfig, storage: LocalCloudStorage,
                  session_id: str, old_host: str, tag: str) -> bool:
    """Two-phase promotion. Authority only committed after server is verified ready.
    
    Phase 1: Publish state=promoting with current_host still as old_host.
             This signals other standbys to not interfere.
    Phase 2: After server ready, atomically commit current_host=me + state=active.
    """
    # Phase 1: Signal intent — DO NOT claim current_host yet
    storage.publish_session(
        session_id=session_id, current_host=old_host,
        server_address="", server_label=config.server_label,
        updated_by=config.player_name, state="promoting"
    )
    print(f"[{tag}] Cleaning up local servers...")
    _force_kill_local_server(config, only_if_owned=True)

    print(f"[{tag}] Downloading latest world...")
    storage.download_world(config.local_world)

    print(f"[{tag}] Launching server...")
    from .minecraft import launch_hidden_server, write_pid, write_server_properties, read_pid

    # H3: Single-instance invariant — refuse to launch if owned Java already alive
    existing = read_pid(config.server_workdir)
    if existing and _is_pid_alive(existing.get("pid", -1)):
        owner = existing.get("host_player", "")
        if owner == config.player_name:
            print(f"[{tag}] WARNING: Owned server pid={existing['pid']} still alive — killing before launch")
            _force_kill_local_server(config, only_if_owned=True)
        else:
            print(f"[{tag}] WARNING: Foreign server pid={existing['pid']} (owner={owner}) alive — killing stale process")
            _force_kill_local_server(config, only_if_owned=False)

    write_server_properties(config.server_workdir)
    process = launch_hidden_server(config.java_path, config.server_jar_path, config.server_workdir)
    write_pid(config.server_workdir, process.pid, host_player=config.player_name)
    print(f"[{tag}] Server launched pid={process.pid}")

    print(f"[{tag}] Waiting for RCON ready...")
    rcon_ok = _wait_for_rcon(timeout=60.0)
    if not rcon_ok:
        print(f"[{tag}] RCON not responding — aborting promotion.")
        _force_kill_local_server(config, only_if_owned=True)
        # Revert to migrating so election can retry
        storage.publish_session(
            session_id=session_id, current_host=old_host,
            server_address="", server_label=config.server_label,
            updated_by=config.player_name, state="migrating"
        )
        raise RuntimeError("Server failed RCON readiness check")

    # Verify with one save-all
    try:
        from .cli import _rcon_client_from_config
        with _rcon_client_from_config() as rcon:
            rcon.command("save-all flush")
        print(f"[{tag}] Save-all verified.")
    except Exception:
        print(f"[{tag}] WARNING: save-all check failed, proceeding anyway.")

    # Phase 2: COMMIT authority — only now claim current_host
    server_addr = config.advertised_address
    storage.publish_session(
        session_id=session_id, current_host=config.player_name,
        server_address=server_addr, server_label=config.server_label,
        updated_by=config.player_name, state="active"
    )
    print(f"[{tag}] Complete. Hosting at {server_addr}")
    return True


def background_orchestrator(config: HelperConfig, allow_host_promotion: bool) -> None:
    storage = LocalCloudStorage(config.cloud, config.world_id)
    missed_heartbeats = 0
    last_save_time = time.time()
    last_cloud_sync_time = time.time()

    promoting = False           # Bug 1: single-entry guard
    was_host = False
    server_ready = False
    last_successful_promotion = 0.0  # Fix 2: cooldown only after success
    COOLDOWN = 30.0
    PROMOTING_TIMEOUT = 90.0    # Fix 4: max time in promoting state

    # Endpoint health monitor (debounced)
    last_endpoint_check = 0.0
    endpoint_fail_count = 0
    ENDPOINT_CHECK_INTERVAL = 30.0
    ENDPOINT_FAIL_THRESHOLD = 3    # Require 3 consecutive failures before republish

    # Demotion safety: suspicion → confirmation → demotion
    host_loss_suspicion = 0
    DEMOTION_CONFIRM_POLLS = 3           # Polls needed before demotion (unhealthy local)
    DEMOTION_CONFIRM_POLLS_HEALTHY = 5   # Polls needed if local RCON is still healthy

    first_run = True
    while True:
        if not first_run:
            time.sleep(5)
        first_run = False

        try:
            try:
                session = storage.session()
            except (FileNotFoundError, PermissionError, OSError):
                continue

            now = datetime.now(timezone.utc)
            updated_at = datetime.fromisoformat(session.updated_at)
            age = (now - updated_at).total_seconds()
            i_am_host = (session.current_host == config.player_name)
            # Fix 2: Cooldown only after successful promotion, bypass if heartbeat dead
            in_cooldown = (time.time() - last_successful_promotion) < COOLDOWN
            if in_cooldown and session.state == "active" and age > 15.0:
                in_cooldown = False  # Host is dead, bypass cooldown

            # Fix 1: Two-phase — i_am_host OR I'm the one promoting
            i_am_promoting = (session.updated_by == config.player_name
                              and session.state == "promoting")

            if i_am_host or i_am_promoting:
                # ---- HOST / PROMOTING BRANCH ----
                if i_am_host and not was_host and not promoting:
                    pass  # Authority was committed, we're now host

                if i_am_host:
                    was_host = True
                    host_loss_suspicion = 0  # We ARE host — clear any suspicion

                if session.state == "active":
                    promoting = False
                    server_ready = True

                    # Heartbeat
                    storage.publish_session(
                        session_id=session.session_id,
                        current_host=session.current_host,
                        server_address=session.server_address,
                        server_label=session.server_label,
                        updated_by=config.player_name, state="active"
                    )

                    # Bug 4: sync only if server ready
                    if server_ready:
                        if time.time() - last_save_time > 20.0:
                            last_save_time = time.time()
                            try:
                                from .cli import _rcon_client_from_config
                                with _rcon_client_from_config() as rcon:
                                    rcon.command("save-all flush")
                            except Exception:
                                pass

                        if time.time() - last_cloud_sync_time > 60.0:
                            last_cloud_sync_time = time.time()
                            try:
                                from .cli import sync_current_world
                                sync_current_world()
                                print("[SYNC] Cloud snapshot updated.")
                            except Exception as e:
                                print(f"[SYNC] Cloud sync failed: {e}")

                        # Debounced endpoint health check
                        if time.time() - last_endpoint_check > ENDPOINT_CHECK_INTERVAL:
                            last_endpoint_check = time.time()
                            try:
                                addr = session.server_address
                                host, port_s = addr.rsplit(":", 1)
                                if not config._probe_port(host, int(port_s), timeout=2.0):
                                    endpoint_fail_count += 1
                                    if endpoint_fail_count >= ENDPOINT_FAIL_THRESHOLD:
                                        new_addr = config.advertised_address
                                        if new_addr != addr:
                                            storage.publish_session(
                                                session_id=session.session_id,
                                                current_host=session.current_host,
                                                server_address=new_addr,
                                                server_label=session.server_label,
                                                updated_by=config.player_name,
                                                state="active"
                                            )
                                            print(f"[ENDPOINT] Republished: {addr} → {new_addr}")
                                        endpoint_fail_count = 0
                                else:
                                    endpoint_fail_count = 0
                            except Exception:
                                pass

                elif session.state in ("migrating", "promoting"):
                    # Bug 1: Don't re-enter if already promoting
                    if promoting:
                        continue
                    if not allow_host_promotion:
                        continue

                    # Transactional handoff: Wait for the required snapshot
                    if session.handoff_snapshot:
                        try:
                            latest_snap = storage.latest()
                            if latest_snap.snapshot_id != session.handoff_snapshot:
                                print(f"[PROMOTION] Waiting for handoff snapshot {session.handoff_snapshot} (current: {latest_snap.snapshot_id})...")
                                continue
                        except Exception:
                            print(f"[PROMOTION] Waiting for cloud to update latest.json...")
                            continue

                    promoting = True
                    server_ready = False
                    try:
                        _do_promotion(config, storage, session.session_id,
                                      session.current_host, "PROMOTION")
                        was_host = True
                        server_ready = True
                        last_successful_promotion = time.time()
                    except Exception as e:
                        print(f"[PROMOTION] Failed: {e}")
                        promoting = False  # Allow retry next cycle

                missed_heartbeats = 0

            else:
                # ---- STANDBY BRANCH ----

                # --- Host-loss confirmation (suspicion → confirmation → demotion) ---
                if was_host:
                    host_loss_suspicion += 1
                    foreign = session.current_host
                    foreign_fresh = age < 15.0
                    foreign_is_newer = session.updated_by != config.player_name

                    # Determine confirmation threshold:
                    # If local server is healthy (RCON responds), require MORE polls
                    confirmation_needed = DEMOTION_CONFIRM_POLLS
                    local_healthy = False
                    if host_loss_suspicion >= 2:
                        try:
                            from .cli import _rcon_client_from_config
                            with _rcon_client_from_config() as rcon:
                                rcon.command("list")
                            local_healthy = True
                            confirmation_needed = DEMOTION_CONFIRM_POLLS_HEALTHY
                        except Exception:
                            pass

                    if host_loss_suspicion < confirmation_needed:
                        print(f"[DEMOTION] Suspicion {host_loss_suspicion}/{confirmation_needed}: "
                              f"foreign_host='{foreign}', fresh={foreign_fresh}, "
                              f"newer={foreign_is_newer}, local_healthy={local_healthy}")
                        continue

                    # Confirmation threshold reached — double-read revalidation
                    try:
                        recheck = storage.session()
                        if recheck.current_host == config.player_name:
                            # False alarm — we're still host
                            print(f"[DEMOTION] Revalidation: I am still host. Cancelling demotion.")
                            host_loss_suspicion = 0
                            continue
                        # Recheck must also show fresh foreign heartbeat (active lease)
                        recheck_age = (datetime.now(timezone.utc) - datetime.fromisoformat(recheck.updated_at)).total_seconds()
                        if recheck_age > 15.0:
                            print(f"[DEMOTION] Revalidation: foreign heartbeat stale ({recheck_age:.0f}s). Cancelling demotion.")
                            host_loss_suspicion = 0
                            continue
                        foreign = recheck.current_host
                    except Exception:
                        print(f"[DEMOTION] Revalidation read failed. Cancelling demotion.")
                        host_loss_suspicion = 0
                        continue

                    # Demotion confirmed — log full diagnostics then act
                    print(f"[DEMOTION] CONFIRMED after {host_loss_suspicion} polls:")
                    print(f"  foreign_host    = '{foreign}'")
                    print(f"  heartbeat_age   = {age:.1f}s")
                    print(f"  foreign_fresh   = {foreign_fresh}")
                    print(f"  foreign_newer   = {foreign_is_newer}")
                    print(f"  local_healthy   = {local_healthy}")
                    print(f"  confirm_polls   = {host_loss_suspicion}/{confirmation_needed}")
                    print(f"  demotion_confirmed = true")

                    was_host = False
                    promoting = False
                    server_ready = False
                    host_loss_suspicion = 0
                    _force_kill_local_server(config, only_if_owned=True)

                # Fix 2: Cooldown only blocks elections, not monitoring
                if in_cooldown:
                    missed_heartbeats = 0
                    continue

                # Fix 4: Promoting timeout — only if BOTH stuck >90s AND promoter heartbeat stale
                if session.state == "promoting" and age < PROMOTING_TIMEOUT:
                    missed_heartbeats = 0  # Someone is actively promoting, wait
                elif session.state == "promoting" and age >= PROMOTING_TIMEOUT:
                    # Promoter stopped refreshing updated_at — likely crashed
                    print(f"[STANDBY] Promoting state stuck for {age:.0f}s with stale heartbeat. Treating as failed.")
                    missed_heartbeats = 3  # Trigger election
                elif session.state == "active" and age > 15.0:
                    missed_heartbeats += 1
                else:
                    missed_heartbeats = 0

                if missed_heartbeats >= 3:
                    # Bug 3: Re-read session to avoid stale data
                    try:
                        fresh = storage.session()
                        f_age = (datetime.now(timezone.utc) - datetime.fromisoformat(fresh.updated_at)).total_seconds()
                        if fresh.state == "promoting" or f_age < 15.0:
                            missed_heartbeats = 0
                            continue
                    except Exception:
                        pass

                    lock_path = storage.world_root / "election.lock"
                    acquired = False
                    try:
                        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        os.write(fd, json.dumps({"time": time.time()}).encode())
                        os.close(fd)
                        acquired = True
                    except FileExistsError:
                        try:
                            # Try to read lease time, fallback to file mtime if legacy empty file
                            lock_text = lock_path.read_text()
                            if lock_text.strip():
                                lock_time = json.loads(lock_text).get("time", 0)
                            else:
                                lock_time = lock_path.stat().st_mtime

                            if time.time() - lock_time > 60:
                                lock_path.unlink()
                                print("[ELECTION] Cleared stale/legacy election lock.")
                            else:
                                print("[ELECTION] Another standby won. Resuming.")
                        except Exception:
                            # If it's completely unreadable but older than 60s, force clear
                            try:
                                if time.time() - lock_path.stat().st_mtime > 60:
                                    lock_path.unlink()
                                    print("[ELECTION] Cleared corrupted election lock.")
                            except OSError:
                                pass
                            print("[ELECTION] Another standby won. Resuming.")

                    if acquired:
                        print("\n[ELECTION] Host dead. ELECTION WON!")

                        try:
                            config.host_player = config.player_name
                            save_config(config)

                            if allow_host_promotion:
                                promoting = True
                                try:
                                    _do_promotion(config, storage, session.session_id,
                                                  session.current_host, "ELECTION")
                                    was_host = True
                                    server_ready = True
                                    last_successful_promotion = time.time()
                                except Exception as e:
                                    print(f"[ELECTION] Promotion failed: {e}")
                                    promoting = False
                            else:
                                storage.publish_session(
                                    session_id=session.session_id,
                                    current_host=config.player_name,
                                    server_address="", server_label=config.server_label,
                                    updated_by=config.player_name, state="migrating"
                                )
                                print("[ELECTION] Auto-promotion disabled.")
                        finally:
                            try:
                                lock_path.unlink()
                            except OSError:
                                pass
                        missed_heartbeats = 0
        except Exception:
            import traceback
            traceback.print_exc()


def _should_cleanup_pid(config: HelperConfig) -> bool:
    from .minecraft import read_pid, pid_file
    pid_data = read_pid(config.server_workdir)
    if pid_data is None:
        return False
    pid = pid_data["pid"]
    platform = pid_data.get("platform", "legacy")
    if platform != "legacy" and platform != sys.platform:
        return False
    if not _is_pid_alive(pid):
        try:
            pid_file(config.server_workdir).unlink()
        except OSError:
            pass
        return False
    owner = pid_data.get("host_player", "")
    if owner == config.host_player:
        return False
    return True




def serve(config: HelperConfig, allow_host_promotion: bool = False) -> None:
    from .minecraft import read_pid

    # Cleanup only genuinely stale/orphan servers on startup (no RCON — immediate kill)
    if _should_cleanup_pid(config):
        print("Cleaning up orphaned server process on startup...")
        _force_kill_local_server(config, only_if_owned=False)

    state = HelperState(config)
    orchestrator = threading.Thread(target=background_orchestrator, args=(config, allow_host_promotion), daemon=True)
    orchestrator.start()
    
    server = ThreadingHTTPServer((config.helper_host, config.helper_port), make_handler(state))
    print(f"Helper API listening on http://{config.helper_host}:{config.helper_port}")
    print(f"Reconnect screen: http://{config.helper_host}:{config.helper_port}/reconnect")
    print(f"Auto-promotion: {'Enabled' if allow_host_promotion else 'Disabled'}")
    
    import atexit
    def cleanup_on_exit():
        """Kill the managed server ONLY if we still own it."""
        try:
            pid_data = read_pid(config.server_workdir)
            if pid_data is None:
                return
            pid = pid_data.get("pid", -1)
            owner = pid_data.get("host_player", "")
            if not _is_pid_alive(pid):
                return
            # Kill if orphaned OR if we own it
            if _should_cleanup_pid(config) or owner == config.player_name:
                print("Cleaning up managed server process on exit...")
                _force_kill_local_server(config, only_if_owned=False)
        except Exception:
            pass

    atexit.register(cleanup_on_exit)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nHelper shutting down...")
    finally:
        cleanup_on_exit()
        atexit.unregister(cleanup_on_exit)
