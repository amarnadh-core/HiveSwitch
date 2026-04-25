from __future__ import annotations

import socket
import struct
from dataclasses import dataclass


AUTH = 3
COMMAND = 2
AUTH_FAILURE_ID = -1


@dataclass
class RconResponse:
    request_id: int
    response_type: int
    body: str


class RconClient:
    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._request_id = 0

    def __enter__(self) -> RconClient:
        self.connect()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def connect(self) -> None:
        self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
        response = self._request(AUTH, self.password)
        if response.request_id == AUTH_FAILURE_ID:
            raise PermissionError("RCON authentication failed.")

    def command(self, command: str) -> str:
        response = self._request(COMMAND, command)
        return response.body

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    def _request(self, request_type: int, body: str) -> RconResponse:
        if self._socket is None:
            raise RuntimeError("RCON client is not connected.")
        self._request_id += 1
        request_id = self._request_id
        payload = struct.pack("<ii", request_id, request_type) + body.encode("utf-8") + b"\x00\x00"
        self._socket.sendall(struct.pack("<i", len(payload)) + payload)
        return self._receive()

    def _receive(self) -> RconResponse:
        if self._socket is None:
            raise RuntimeError("RCON client is not connected.")
        header = self._read_exact(4)
        (length,) = struct.unpack("<i", header)
        packet = self._read_exact(length)
        request_id, response_type = struct.unpack("<ii", packet[:8])
        body = packet[8:-2].decode("utf-8", errors="replace")
        return RconResponse(request_id=request_id, response_type=response_type, body=body)

    def _read_exact(self, size: int) -> bytes:
        if self._socket is None:
            raise RuntimeError("RCON client is not connected.")
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = self._socket.recv(remaining)
            if not chunk:
                raise ConnectionError("RCON connection closed unexpectedly.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

