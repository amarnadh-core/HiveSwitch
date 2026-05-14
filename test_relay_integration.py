"""Self-contained integration test: RemoteCloudStorage <-> Cloud Relay Server."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from cloud_relay.config import RelayConfig, save_config
from helper_app.serverless_mc.remote_storage import RemoteCloudStorage


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_for_health(url: str, api_key: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        req = urllib.request.Request(f"{url}/health")
        req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=1.0) as resp:
                if resp.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    raise RuntimeError("Relay did not become healthy in time")


def main() -> None:
    tmp_root = Path(tempfile.mkdtemp(prefix="relay_it_"))
    fake_world = Path(tempfile.mkdtemp(prefix="relay_test_world_"))
    download_dir = Path(tempfile.mkdtemp(prefix="relay_test_download_"))
    proc: subprocess.Popen | None = None

    try:
        api_key = "integration-test-key"
        port = free_port()
        relay_url = f"http://127.0.0.1:{port}"
        config_path = tmp_root / "cloud_relay.json"
        data_dir = tmp_root / "relay_data"
        save_config(
            RelayConfig(
                host="127.0.0.1",
                port=port,
                data_dir=str(data_dir),
                api_key=api_key,
                max_upload_bytes=10 * 1024 * 1024,
                max_snapshots_per_world=20,
            ),
            config_path,
        )

        proc = subprocess.Popen(
            [sys.executable, "-m", "cloud_relay.server", "--config", str(config_path)],
            cwd=Path.cwd(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        wait_for_health(relay_url, api_key)

        world_id = "integration-test"
        storage = RemoteCloudStorage(relay_url, world_id, api_key)

        print("=== Test 1: Session publish/read ===")
        info = storage.publish_session(
            session_id="test-session",
            current_host="tester",
            server_address="192.168.1.99:25565",
            server_label="Test Host",
            updated_by="tester",
            state="active",
        )
        assert info.current_host == "tester"
        read_back = storage.session()
        assert read_back.current_host == "tester"
        assert read_back.state == "active"
        print("  PASS: session round-trip")

        print("=== Test 2: Full snapshot upload ===")
        (fake_world / "level.dat").write_bytes(b"fake-level-data-12345")
        (fake_world / "region").mkdir()
        (fake_world / "region" / "r.0.0.mca").write_bytes(b"A" * 1000)
        (fake_world / "region" / "r.1.0.mca").write_bytes(b"B" * 500)

        full_info = storage.upload_world(fake_world, "tester")
        assert full_info.type == "full"
        print(f"  PASS: uploaded full snapshot {full_info.snapshot_id}")

        print("=== Test 3: Latest pointer ===")
        latest = storage.latest()
        assert latest.snapshot_id == full_info.snapshot_id
        print(f"  PASS: latest = {latest.snapshot_id}")

        print("=== Test 4: Validate world ===")
        problems = storage.validate_world(fake_world)
        assert problems == [], f"Unexpected problems: {problems}"
        print("  PASS: validation clean")

        print("=== Test 5: Delta snapshot upload ===")
        (fake_world / "region" / "r.0.0.mca").write_bytes(b"C" * 1000)
        delta_info = storage.upload_world(fake_world, "tester")
        assert delta_info.type == "delta"
        assert delta_info.base == full_info.snapshot_id
        print(f"  PASS: uploaded delta snapshot {delta_info.snapshot_id}")

        print("=== Test 6: Download world ===")
        dl_info = storage.download_world(download_dir)
        assert dl_info.snapshot_id == delta_info.snapshot_id
        assert (download_dir / "level.dat").read_bytes() == b"fake-level-data-12345"
        assert (download_dir / "region" / "r.0.0.mca").read_bytes() == b"C" * 1000
        assert (download_dir / "region" / "r.1.0.mca").read_bytes() == b"B" * 500
        print(f"  PASS: downloaded and verified {dl_info.snapshot_id}")

        print("=== Test 7: Election lock ===")
        won, _ = storage.try_election("tester")
        assert won, "Expected to win election"
        won2, _ = storage.try_election("other-player")
        assert not won2, "Expected second candidate to lose"
        assert storage.release_election("tester")
        print("  PASS: election lock/release works")

        print("\n=== ALL 7 TESTS PASSED ===")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(fake_world, ignore_errors=True)
        shutil.rmtree(download_dir, ignore_errors=True)
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
