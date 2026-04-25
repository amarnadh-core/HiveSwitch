from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008
PID_FILE_NAME = "serverless-helper.pid"


def load_server_properties(server_dir: Path) -> dict[str, str]:
    path = server_dir / "server.properties"
    properties: dict[str, str] = {}
    if not path.exists():
        return properties
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        properties[key] = value
    return properties


def save_server_properties(server_dir: Path, updates: dict[str, str]) -> Path:
    server_dir.mkdir(parents=True, exist_ok=True)
    path = server_dir / "server.properties"
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    seen: set[str] = set()
    output: list[str] = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key, _ = stripped.split("=", 1)
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)

    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return path


def set_auth_mode(server_dir: Path, mode: str) -> Path:
    if mode == "offline":
        return save_server_properties(
            server_dir,
            {
                "online-mode": "false",
                "enforce-secure-profile": "false",
            },
        )
    if mode == "online":
        return save_server_properties(
            server_dir,
            {
                "online-mode": "true",
                "enforce-secure-profile": "true",
            },
        )
    raise ValueError(f"Unknown auth mode: {mode}")


def set_rcon(server_dir: Path, enabled: bool, password: str = "serverless-phase1", port: int = 25575) -> Path:
    return save_server_properties(
        server_dir,
        {
            "enable-rcon": "true" if enabled else "false",
            "rcon.password": password if enabled else "",
            "rcon.port": str(port),
        },
    )


def set_server_port(server_dir: Path, port: int, server_ip: str = "") -> Path:
    return save_server_properties(
        server_dir,
        {
            "server-ip": server_ip,
            "server-port": str(port),
            "query.port": str(port),
        },
    )


def write_eula(server_dir: Path, accepted: bool) -> Path:
    server_dir.mkdir(parents=True, exist_ok=True)
    path = server_dir / "eula.txt"
    path.write_text(
        "\n".join(
            [
                "# By changing the setting below to TRUE you are indicating your agreement to the Minecraft EULA.",
                "# https://aka.ms/MinecraftEULA",
                f"eula={'true' if accepted else 'false'}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def launch_hidden_server(java_path: str, server_jar: Path, server_dir: Path, memory: str = "2G") -> subprocess.Popen:
    if not server_jar.exists():
        raise FileNotFoundError(f"Server jar not found: {server_jar}")
    server_dir.mkdir(parents=True, exist_ok=True)
    command = [
        java_path,
        f"-Xmx{memory}",
        f"-Xms{memory}",
        "-jar",
        str(server_jar),
        "nogui",
    ]
    stdout = (server_dir / "serverless-helper.stdout.log").open("ab")
    stderr = (server_dir / "serverless-helper.stderr.log").open("ab")
    if sys.platform == "win32":
        creationflags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS
        return subprocess.Popen(
            command,
            cwd=str(server_dir),
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
            close_fds=True,
        )
    else:
        import os
        return subprocess.Popen(
            command,
            cwd=str(server_dir),
            stdout=stdout,
            stderr=stderr,
            preexec_fn=os.setsid,
            close_fds=True,
        )


def pid_file(server_dir: Path) -> Path:
    return server_dir / PID_FILE_NAME


def write_pid(server_dir: Path, pid: int, host_player: str = "") -> None:
    data = {
        "pid": pid,
        "platform": sys.platform,
        "host_player": host_player,
    }
    pid_file(server_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_pid(server_dir: Path) -> dict | None:
    path = pid_file(server_dir)
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8").strip()
        if content.startswith("{"):
            return json.loads(content)
        else:
            # Legacy simple integer fallback
            return {"pid": int(content), "platform": "legacy"}
    except (ValueError, json.JSONDecodeError):
        return None


def write_server_properties(server_dir: Path, server_ip: str = "0.0.0.0", port: int = 25565) -> Path:
    server_dir.mkdir(parents=True, exist_ok=True)
    path = server_dir / "server.properties"
    if path.exists():
        return path
    path.write_text(
        "\n".join(
            [
                "online-mode=true",
                f"server-ip={server_ip}",
                f"server-port={port}",
                "enable-command-block=false",
                "spawn-protection=0",
                "white-list=false",
                "motd=Serverless MC Prototype",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path
