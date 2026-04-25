from __future__ import annotations

import shutil
import subprocess


def join_zerotier(network_id: str) -> str:
    if not network_id:
        return "No ZeroTier network configured."
    executable = shutil.which("zerotier-cli")
    if not executable:
        return "zerotier-cli was not found on PATH. Install ZeroTier or join the network manually."
    result = subprocess.run([executable, "join", network_id], check=False, capture_output=True, text=True)
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        return f"ZeroTier join failed: {output}"
    return output or f"Joined ZeroTier network {network_id}."

