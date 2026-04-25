"""Idempotent device action tests."""

import hashlib
import json
from types import SimpleNamespace

from backend.agent.actions.device_actions import connect_wifi, install_apk, push_resources
from backend.agent.pipeline_engine import StepContext


class FakeADB:
    adb_path = "adb"

    def __init__(self):
        self.shell_calls = []
        self.push_calls = []

    def shell(self, serial, command, timeout=10):
        self.shell_calls.append(command)
        return ""

    def push(self, serial, local, remote):
        self.push_calls.append((local, remote))


def _ctx(adb, params):
    return StepContext(
        adb=adb,
        serial="SERIAL001",
        params=params,
        run_id=1,
        step_id=0,
        logger=SimpleNamespace(info=lambda *a, **k: None, warn=lambda *a, **k: None),
    )


def test_connect_wifi_skips_when_target_ssid_is_already_connected():
    class WifiADB(FakeADB):
        def shell(self, serial, command, timeout=10):
            self.shell_calls.append(command)
            if "wifi status" in command:
                return "Wi-Fi is enabled\nSSID: TestNet\n"
            return ""

    adb = WifiADB()
    result = connect_wifi(_ctx(adb, {"ssid": "TestNet", "password": "secret"}))

    assert result.success is True
    assert result.skipped is True
    assert "Already connected" in result.skip_reason
    assert not any("connect-network" in cmd for cmd in adb.shell_calls)


def test_install_apk_skips_when_required_version_is_installed(monkeypatch):
    class ApkADB(FakeADB):
        def shell(self, serial, command, timeout=10):
            self.shell_calls.append(command)
            return "versionName=1.2.3\n"

    monkeypatch.setattr(
        "backend.agent.actions.device_actions.subprocess.run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("install should not run")),
    )

    adb = ApkADB()
    result = install_apk(_ctx(adb, {
        "apk_path": "/nfs/app.apk",
        "pkg_name": "com.example.app",
        "required_version": "1.2.3",
    }))

    assert result.success is True
    assert result.skipped is True
    assert "already installed" in result.skip_reason


def test_push_resources_legacy_files_mode_still_pushes_files():
    adb = FakeADB()
    result = push_resources(_ctx(adb, {
        "files": [{"local": "/tmp/a.txt", "remote": "/sdcard/a.txt", "chmod": "755"}]
    }))

    assert result.success is True
    assert adb.push_calls == [("/tmp/a.txt", "/sdcard/a.txt")]
    assert any("chmod 755 /sdcard/a.txt" in cmd for cmd in adb.shell_calls)


def test_push_resources_bundle_mode_skips_when_marker_matches(tmp_path):
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"bundle")
    bundle_sha = hashlib.sha256(bundle.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "name": "audio",
        "bundle_sha256": bundle_sha,
        "file_count": 2,
        "total_size_bytes": 6,
    }), encoding="utf-8")

    class MarkerADB(FakeADB):
        def shell(self, serial, command, timeout=10):
            self.shell_calls.append(command)
            if ".stp_bundle_sha256" in command:
                return bundle_sha
            return ""

    adb = MarkerADB()
    result = push_resources(_ctx(adb, {
        "bundle": str(bundle),
        "manifest": str(manifest),
        "remote_dir": "/sdcard/test_resources",
    }))

    assert result.success is True
    assert result.skipped is True
    assert adb.push_calls == []


def test_push_resources_bundle_mode_rejects_bundle_sha_mismatch(tmp_path):
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"bundle")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "name": "audio",
        "bundle_sha256": "0" * 64,
        "file_count": 2,
        "total_size_bytes": 6,
    }), encoding="utf-8")

    adb = FakeADB()
    result = push_resources(_ctx(adb, {
        "bundle": str(bundle),
        "manifest": str(manifest),
        "remote_dir": "/sdcard/test_resources",
    }))

    assert result.success is False
    assert "bundle_sha256 mismatch" in result.error_message
    assert adb.push_calls == []
