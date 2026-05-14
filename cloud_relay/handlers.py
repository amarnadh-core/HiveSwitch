"""HTTP request handlers for the Cloud Relay Server.

Routes:
    GET  /health
    GET  /worlds/{world_id}/session
    PUT  /worlds/{world_id}/session
    GET  /worlds/{world_id}/latest
    PUT  /worlds/{world_id}/latest
    GET  /worlds/{world_id}/snapshots
    GET  /worlds/{world_id}/snapshots/{snapshot_id}/meta
    PUT  /worlds/{world_id}/snapshots/{snapshot_id}/meta
    GET  /worlds/{world_id}/snapshots/{snapshot_id}/manifest
    PUT  /worlds/{world_id}/snapshots/{snapshot_id}/manifest
    POST /worlds/{world_id}/snapshots/{snapshot_id}/complete
    GET  /worlds/{world_id}/snapshots/{snapshot_id}/files/{path...}
    PUT  /worlds/{world_id}/snapshots/{snapshot_id}/files/{path...}
    POST /worlds/{world_id}/election
    GET  /worlds/{world_id}/election
    DELETE /worlds/{world_id}/election
"""

from __future__ import annotations

import json
import re
import time
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import unquote, urlparse

from .storage import RelayStorage


# Route patterns — order matters (most specific first)
_ROUTES: list[tuple[str, re.Pattern]] = [
    ("health",              re.compile(r"^/health$")),
    ("session",             re.compile(r"^/worlds/(?P<world_id>[^/]+)/session$")),
    ("latest",              re.compile(r"^/worlds/(?P<world_id>[^/]+)/latest$")),
    ("snapshot_list",       re.compile(r"^/worlds/(?P<world_id>[^/]+)/snapshots$")),
    ("snapshot_meta",       re.compile(r"^/worlds/(?P<world_id>[^/]+)/snapshots/(?P<snapshot_id>[^/]+)/meta$")),
    ("snapshot_manifest",   re.compile(r"^/worlds/(?P<world_id>[^/]+)/snapshots/(?P<snapshot_id>[^/]+)/manifest$")),
    ("snapshot_complete",   re.compile(r"^/worlds/(?P<world_id>[^/]+)/snapshots/(?P<snapshot_id>[^/]+)/complete$")),
    ("snapshot_file",       re.compile(r"^/worlds/(?P<world_id>[^/]+)/snapshots/(?P<snapshot_id>[^/]+)/files/(?P<file_path>.+)$")),
    ("election",            re.compile(r"^/worlds/(?P<world_id>[^/]+)/election$")),
]


def _match_route(path: str) -> tuple[str | None, dict[str, str]]:
    """Match a URL path against known routes. Returns (route_name, params)."""
    for name, pattern in _ROUTES:
        m = pattern.match(path)
        if m:
            return name, {k: unquote(v) for k, v in m.groupdict().items()}
    return None, {}


def make_handler(storage: RelayStorage, api_key: str,
                 max_upload_bytes: int = 50 * 1024 * 1024,
                 max_snapshots_per_world: int = 20):
    """Create a request handler class bound to the given storage and API key."""

    class RelayHandler(BaseHTTPRequestHandler):

        # -- Helpers --------------------------------------------------------

        def _json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _binary(self, status: int, data: bytes, content_type: str = "application/octet-stream") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return b""
            return self.rfile.read(length)

        def _read_json(self) -> dict | list:
            body = self._read_body()
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))

        def _check_auth(self) -> bool:
            """Verify Bearer token. Returns True if authorized."""
            if not api_key:
                return True  # No key configured = open access
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {api_key}":
                return True
            self._json(401, {"error": "unauthorized", "message": "Invalid or missing API key"})
            return False

        def log_message(self, format, *args):
            """Suppress default stderr logging for clean output."""
            pass

        # -- GET ------------------------------------------------------------

        def do_GET(self) -> None:
            if not self._check_auth():
                return

            path = urlparse(self.path).path
            route, params = _match_route(path)

            try:
                if route == "health":
                    self._json(200, {"status": "ok", "time": time.time()})

                elif route == "session":
                    data = storage.get_session(params["world_id"])
                    if data is None:
                        self._json(404, {"error": "not_found", "message": "No session for this world"})
                    else:
                        self._json(200, data)

                elif route == "latest":
                    data = storage.get_latest(params["world_id"])
                    if data is None:
                        self._json(404, {"error": "not_found", "message": "No latest snapshot"})
                    else:
                        self._json(200, data)

                elif route == "snapshot_list":
                    ids = storage.list_snapshots(params["world_id"])
                    self._json(200, {"snapshots": ids})

                elif route == "snapshot_meta":
                    data = storage.get_snapshot_meta(params["world_id"], params["snapshot_id"])
                    if data is None:
                        self._json(404, {"error": "not_found"})
                    else:
                        self._json(200, data)

                elif route == "snapshot_manifest":
                    data = storage.get_snapshot_manifest(params["world_id"], params["snapshot_id"])
                    if data is None:
                        self._json(404, {"error": "not_found"})
                    else:
                        self._json(200, data)

                elif route == "snapshot_file":
                    data = storage.get_file(params["world_id"], params["snapshot_id"], params["file_path"])
                    if data is None:
                        self._json(404, {"error": "not_found"})
                    else:
                        self._binary(200, data)

                elif route == "election":
                    data = storage.get_election(params["world_id"])
                    if data is None:
                        self._json(200, {"locked": False})
                    else:
                        self._json(200, {"locked": True, **data})

                else:
                    self._json(404, {"error": "not_found", "message": f"Unknown route: {path}"})
            except ValueError as e:
                self._json(400, {"error": "bad_request", "message": str(e)})

        # -- PUT ------------------------------------------------------------

        def do_PUT(self) -> None:
            if not self._check_auth():
                return

            path = urlparse(self.path).path
            route, params = _match_route(path)

            if route == "session":
                data = self._read_json()
                storage.put_session(params["world_id"], data)
                self._json(200, {"status": "ok"})

            elif route == "latest":
                data = self._read_json()
                storage.put_latest(params["world_id"], data)
                self._json(200, {"status": "ok"})

            elif route == "snapshot_meta":
                data = self._read_json()
                storage.put_snapshot_meta(params["world_id"], params["snapshot_id"], data)
                self._json(200, {"status": "ok"})

            elif route == "snapshot_manifest":
                data = self._read_json()
                storage.put_snapshot_manifest(params["world_id"], params["snapshot_id"], data)
                self._json(200, {"status": "ok"})

            elif route == "snapshot_file":
                body = self._read_body()
                try:
                    storage.put_file(
                        params["world_id"], params["snapshot_id"],
                        params["file_path"], body, max_upload_bytes
                    )
                except ValueError as e:
                    self._json(400, {"error": "bad_request", "message": str(e)})
                    return
                self._json(200, {"status": "ok", "size": len(body)})

            else:
                self._json(404, {"error": "not_found", "message": f"Unknown route: {path}"})

        # -- POST -----------------------------------------------------------

        def do_POST(self) -> None:
            if not self._check_auth():
                return

            path = urlparse(self.path).path
            route, params = _match_route(path)

            if route == "snapshot_complete":
                wid = params["world_id"]
                sid = params["snapshot_id"]
                storage.mark_snapshot_complete(wid, sid)
                # Auto-cleanup old snapshots
                deleted = storage.cleanup_snapshots(wid, keep=max_snapshots_per_world)
                self._json(200, {"status": "ok", "cleaned_up": deleted})

            elif route == "election":
                body = self._read_json()
                candidate_name = body.get("candidate", "unknown")
                won, lock_data = storage.try_election(
                    params["world_id"],
                    {"candidate": candidate_name, **body}
                )
                status = 200 if won else 409
                self._json(status, {"won": won, "lock": lock_data})

            else:
                self._json(404, {"error": "not_found", "message": f"Unknown route: {path}"})

        # -- DELETE ---------------------------------------------------------

        def do_DELETE(self) -> None:
            if not self._check_auth():
                return

            path = urlparse(self.path).path
            route, params = _match_route(path)

            if route == "election":
                body = self._read_json() if int(self.headers.get("Content-Length", "0")) > 0 else {}
                candidate = body.get("candidate", "")
                released = storage.release_election(params["world_id"], candidate)
                if released:
                    self._json(200, {"status": "released"})
                else:
                    self._json(409, {"status": "conflict", "message": "Lock owned by another candidate"})

            else:
                self._json(404, {"error": "not_found", "message": f"Unknown route: {path}"})

    return RelayHandler
