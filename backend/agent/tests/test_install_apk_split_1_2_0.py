"""install_apk v1.2.0：split APK（install-multiple）与装后版本回读。

覆盖：
- apk_paths / apk_dir 都走 ``adb install-multiple -r``，base.apk 优先；
- 单 apk_path 仍走 ``adb install -r``（向后兼容）；
- required_version 已相等时 skip；
- 装后 versionName 不等于目标时 fail（v1.2.0 新增回读）；
- 路径不存在 fail-fast。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load():
    adb_path = SCRIPTS / "install_apk" / "_adb.py"
    spec = importlib.util.spec_from_file_location("_adb_install_apk_v120", adb_path)
    adb = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(adb)
    old = sys.modules.get("_adb")
    sys.modules["_adb"] = adb
    try:
        spec = importlib.util.spec_from_file_location(
            "install_apk_v120", SCRIPTS / "install_apk" / "install_apk.py"
        )
        script = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(script)
        return script
    finally:
        if old is None:
            sys.modules.pop("_adb", None)
        else:
            sys.modules["_adb"] = old


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_adb(script, monkeypatch, *, before: str, after: str):
    calls: list[list[str]] = []
    state = {"version": before}

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        if any("dumpsys" in str(part) for part in cmd):
            return _Result(stdout=f"versionName={state['version']}\n")
        state["version"] = after
        return _Result(stdout="Success\n")

    monkeypatch.setattr(script.subprocess, "run", run)
    return calls


def _run(script, monkeypatch, params: dict):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(params))
    script.main()


def _out(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_split_install_uses_install_multiple(monkeypatch, tmp_path, capsys):
    script = _load()
    base = tmp_path / "base.apk"
    split = tmp_path / "split_chrome.apk"
    base.write_bytes(b"base")
    split.write_bytes(b"split")
    calls = _fake_adb(script, monkeypatch, before="1.0.0", after="2.0.0")

    _run(
        script,
        monkeypatch,
        {
            "apk_paths": [str(base), str(split)],
            "pkg_name": "com.android.chrome",
            "required_version": "2.0.0",
        },
    )

    install_cmds = [c for c in calls if "install" in c[3] or "install-multiple" in c[3]]
    assert install_cmds, calls
    cmd = install_cmds[0]
    assert cmd[3] == "install-multiple"
    assert "-r" in cmd
    assert cmd[-2:] == [str(base), str(split)]
    payload = _out(capsys)
    assert payload["success"] is True
    assert payload["metrics"]["install_mode"] == "multiple"
    assert payload["metrics"]["installed_version"] == "2.0.0"


def test_apk_dir_expands_and_base_first(monkeypatch, tmp_path, capsys):
    script = _load()
    for name in ("split_chrome.apk", "base.apk", "split_config.en.apk"):
        (tmp_path / name).write_bytes(b"x")
    calls = _fake_adb(script, monkeypatch, before="1.0.0", after="2.0.0")

    _run(
        script,
        monkeypatch,
        {"apk_dir": str(tmp_path), "pkg_name": "com.android.chrome", "required_version": "2.0.0"},
    )

    cmd = [c for c in calls if "install-multiple" in c][0]
    apk_args = cmd[cmd.index("install-multiple") + 2 :]
    assert Path(apk_args[0]).name == "base.apk"
    assert {Path(p).name for p in apk_args} == {
        "base.apk",
        "split_chrome.apk",
        "split_config.en.apk",
    }
    assert _out(capsys)["success"] is True


def test_single_apk_still_uses_install(monkeypatch, tmp_path, capsys):
    script = _load()
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    calls = _fake_adb(script, monkeypatch, before="1.0.0", after="2.0.0")

    _run(script, monkeypatch, {"apk_path": str(apk), "pkg_name": "com.example", "required_version": "2.0.0"})

    cmd = [c for c in calls if len(c) > 3 and c[3] == "install"][0]
    assert cmd[4] == "-r" and cmd[5] == str(apk)
    assert _out(capsys)["metrics"]["install_mode"] == "single"


def test_skip_when_installed_version_matches(monkeypatch, tmp_path, capsys):
    script = _load()
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    calls = _fake_adb(script, monkeypatch, before="2.0.0", after="2.0.0")

    _run(script, monkeypatch, {"apk_path": str(apk), "pkg_name": "com.example", "required_version": "2.0.0"})

    assert not any(c[3] == "install" for c in calls if len(c) > 3)
    payload = _out(capsys)
    assert payload["skipped"] is True
    assert payload["metrics"]["installed_version"] == "2.0.0"


def test_post_install_version_mismatch_fails(monkeypatch, tmp_path, capsys):
    script = _load()
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"x")
    _fake_adb(script, monkeypatch, before="1.0.0", after="1.0.0")

    _run(script, monkeypatch, {"apk_path": str(apk), "pkg_name": "com.example", "required_version": "2.0.0"})

    payload = _out(capsys)
    assert payload["success"] is False
    assert "post-install version mismatch" in payload["error_message"]
    assert payload["metrics"]["installed_version"] == "1.0.0"


def test_missing_apk_fails_fast(monkeypatch, tmp_path, capsys):
    script = _load()
    _fake_adb(script, monkeypatch, before="1.0.0", after="1.0.0")

    _run(script, monkeypatch, {"apk_path": str(tmp_path / "missing.apk")})

    payload = _out(capsys)
    assert payload["success"] is False
    assert "APK not found" in payload["error_message"]
