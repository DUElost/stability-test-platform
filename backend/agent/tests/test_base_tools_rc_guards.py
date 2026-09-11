# -*- coding: utf-8 -*-
"""基础小工具 rc 守门回归（#812）——clean_env / push_resources / fill_storage v1.0.1。

加载方式对齐 test_powercycle_scripts.py：importlib + sys.path 注入；
本族脚本使用 ``_adb`` 辅助模块（非 _lib），加载前后清 ``sys.modules['_adb']``
防与同目录其他脚本串库。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_adb", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_adb", None)
        sys.path.remove(str(path.parent))


class _Output:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def __call__(self, success, **kwargs) -> None:
        self.payloads.append({"success": success, **kwargs})

    @property
    def last(self) -> dict:
        assert self.payloads, "output_result was not called"
        return self.payloads[-1]


def _cp(returncode: int = 0, stdout: str = "", stderr: str = "") -> CompletedProcess:
    return CompletedProcess(args=["adb"], returncode=returncode, stdout=stdout, stderr=stderr)


# ── clean_env v1.0.1 ─────────────────────────────────────────────────────────


def _prepare_clean_env(monkeypatch, mod, params, respond):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: params)
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod, "adb_shell_quiet", respond)
    return capture


def test_clean_env_uninstall_failure_reports_error(monkeypatch):
    mod = _load("clean_env_v101", "clean_env/v1.0.1/clean_env.py")
    capture = _prepare_clean_env(
        monkeypatch,
        mod,
        {"uninstall_packages": ["com.fail.app"]},
        lambda cmd, timeout=30: _cp(1, "Failure [DELETE_FAILED_INTERNAL_ERROR]"),
    )

    mod.main()

    assert capture.last["success"] is False
    assert "Failed to uninstall com.fail.app" in capture.last["error_message"]
    assert capture.last["metrics"]["uninstalled"] == 0


def test_clean_env_uninstall_not_installed_is_skipped(monkeypatch):
    mod = _load("clean_env_v101_not_installed", "clean_env/v1.0.1/clean_env.py")
    capture = _prepare_clean_env(
        monkeypatch,
        mod,
        {"uninstall_packages": ["com.absent.app"]},
        lambda cmd, timeout=30: _cp(1, "Failure [not installed for 0]"),
    )

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["uninstalled"] == 0


def test_clean_env_uninstall_success_counts(monkeypatch):
    mod = _load("clean_env_v101_success", "clean_env/v1.0.1/clean_env.py")
    capture = _prepare_clean_env(
        monkeypatch,
        mod,
        {"uninstall_packages": ["com.ok.app"]},
        lambda cmd, timeout=30: _cp(0, "Success"),
    )

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["uninstalled"] == 1


def test_clean_env_clear_logs_rc_failure_reports_error(monkeypatch):
    mod = _load("clean_env_v101_logs", "clean_env/v1.0.1/clean_env.py")

    def respond(cmd, timeout=30):
        if "rm -rf" in cmd:
            return _cp(1, "", "rm: /data/aee_exp: Permission denied")
        return _cp(0, "")

    capture = _prepare_clean_env(monkeypatch, mod, {"clear_logs": True}, respond)

    mod.main()

    assert capture.last["success"] is False
    assert "Failed to clear" in capture.last["error_message"]
    assert capture.last["metrics"]["logs_cleared"] == 0


# ── fill_storage v1.0.1 ──────────────────────────────────────────────────────

_DF_HEADER = "Filesystem     1K-blocks    Used Available Use% Mounted on"


def _df_output(used_kb: int) -> str:
    return f"{_DF_HEADER}\n/dev/block/sda  100000  {used_kb}  {100000 - used_kb}  {used_kb // 1000}% /data\n"


def _prepare_fill_storage(monkeypatch, mod, params, respond):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(mod, "params", lambda: params)
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod, "adb_shell_quiet", respond)
    return capture


def test_fill_storage_dd_rc_failure_reports_error(monkeypatch):
    mod = _load("fill_storage_v101_dd_fail", "fill_storage/v1.0.1/fill_storage.py")

    def respond(cmd, timeout=30):
        if "df /data" in cmd:
            return _cp(0, _df_output(10_000))
        if "dd if=/dev/zero" in cmd:
            return _cp(1, "", "dd: write error: No space left on device")
        return _cp(0, "")

    capture = _prepare_fill_storage(monkeypatch, mod, {"target_percentage": 90}, respond)

    mod.main()

    assert capture.last["success"] is False
    assert "dd failed" in capture.last["error_message"]


def test_fill_storage_df_below_target_reports_error(monkeypatch):
    mod = _load("fill_storage_v101_df_low", "fill_storage/v1.0.1/fill_storage.py")
    state = {"df_calls": 0}

    def respond(cmd, timeout=30):
        if "df /data" in cmd:
            state["df_calls"] += 1
            # 首次 10%（need > 0），回读仍 50% < target 90%——dd 退出码 0 但没写满
            return _cp(0, _df_output(10_000 if state["df_calls"] == 1 else 50_000))
        return _cp(0, "")

    capture = _prepare_fill_storage(monkeypatch, mod, {"target_percentage": 90}, respond)

    mod.main()

    assert capture.last["success"] is False
    assert "fill insufficient" in capture.last["error_message"]
    assert capture.last["metrics"]["actual_pct"] == 50


def test_fill_storage_success_reports_actual_pct(monkeypatch):
    mod = _load("fill_storage_v101_ok", "fill_storage/v1.0.1/fill_storage.py")
    state = {"df_calls": 0}

    def respond(cmd, timeout=30):
        if "df /data" in cmd:
            state["df_calls"] += 1
            return _cp(0, _df_output(10_000 if state["df_calls"] == 1 else 95_000))
        return _cp(0, "")

    capture = _prepare_fill_storage(monkeypatch, mod, {"target_percentage": 90}, respond)

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["metrics"]["actual_pct"] == 95


# ── push_resources v1.0.1 ────────────────────────────────────────────────────


def _make_bundle(tmp_path):
    bundle = tmp_path / "bundle.tar.gz"
    bundle.write_bytes(b"payload")
    sha = hashlib.sha256(b"payload").hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {"bundle_sha256": sha, "name": "t", "file_count": 2, "total_size_bytes": 7}
        ),
        encoding="utf-8",
    )
    return bundle, manifest, sha


def _prepare_push_resources(monkeypatch, mod, bundle, manifest, respond):
    capture = _Output()
    monkeypatch.setattr(mod, "device_serial", lambda: "SERIAL")
    monkeypatch.setattr(
        mod,
        "params",
        lambda: {
            "bundle": str(bundle),
            "manifest": str(manifest),
            "remote_dir": "/sdcard/test_resources",
            "skip_if_match": False,
        },
    )
    monkeypatch.setattr(mod, "output_result", capture)
    monkeypatch.setattr(mod, "adb_push", lambda local, remote, timeout=120: None)
    monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=30: "")
    monkeypatch.setattr(mod, "adb_shell_quiet", respond)
    return capture


def test_push_resources_unpack_rc_failure(monkeypatch, tmp_path):
    mod = _load("push_resources_v101_unpack", "push_resources/v1.0.1/push_resources.py")
    bundle, manifest, _sha = _make_bundle(tmp_path)

    def respond(cmd, timeout=30):
        if "tar xf" in cmd:
            return _cp(1, "", "tar: corrupt archive")
        return _cp(0, "")

    capture = _prepare_push_resources(monkeypatch, mod, bundle, manifest, respond)

    mod.main()

    assert capture.last["success"] is False
    assert "unpack failed" in capture.last["error_message"]


def test_push_resources_marker_mismatch(monkeypatch, tmp_path):
    mod = _load("push_resources_v101_marker", "push_resources/v1.0.1/push_resources.py")
    bundle, manifest, _sha = _make_bundle(tmp_path)

    def respond(cmd, timeout=30):
        if "tar xf" in cmd:
            return _cp(0, "")
        if "cat " in cmd and ".stp_bundle_sha256" in cmd:
            return _cp(0, "stale-sha\n")
        return _cp(0, "")

    capture = _prepare_push_resources(monkeypatch, mod, bundle, manifest, respond)

    mod.main()

    assert capture.last["success"] is False
    assert "marker verification failed" in capture.last["error_message"]


def test_push_resources_success(monkeypatch, tmp_path):
    mod = _load("push_resources_v101_ok", "push_resources/v1.0.1/push_resources.py")
    bundle, manifest, sha = _make_bundle(tmp_path)

    def respond(cmd, timeout=30):
        if "tar xf" in cmd:
            return _cp(0, "")
        if "cat " in cmd and ".stp_bundle_sha256" in cmd:
            return _cp(0, f"{sha}\n")
        return _cp(0, "")

    capture = _prepare_push_resources(monkeypatch, mod, bundle, manifest, respond)

    mod.main()

    assert capture.last["success"] is True
    assert capture.last["file_count"] == 2
