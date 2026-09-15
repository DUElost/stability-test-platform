"""flash_firmware v1.3.16：门控回落面收敛为 wrapper 窄面（ADR-0037 D5 / #2133）。

守什么：
- `_set_authorized` 直写失败后回调 `stp-agent-priv usb-authorized`
  （argv 形态精确断言：--port/--value 成对），**不再有 `sudo -n sh -c`**；
- wrapper 不可用/失败 → (False, no-priv-face / wrapper-failed)，门控降级
  并在报告里如实记录（非致命）；
- 能力探针带子命令本体（#2011 教训）并按进程缓存；
- 门控集成：同一次运行 direct 与 wrapper 两条路径都工作（假树，路径为
  目录的 authorized 强制走回落）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "flash_firmware" / "v1.3.16"
)

spec = importlib.util.spec_from_file_location(
    "flash_firmware_v1316", _SCRIPT_DIR / "flash_firmware.py"
)
ff = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(ff)


@pytest.fixture(autouse=True)
def _reset_priv_cache():
    ff._priv_wrapper_cache[0] = None
    yield
    ff._priv_wrapper_cache[0] = None


class _P:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _Recorder:
    def __init__(self, rc: int = 0):
        self.rc = rc
        self.calls: "list[list[str]]" = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return _P(returncode=self.rc)


def _make_port(base: Path, port: str, pid: str = "2000",
               authorized_file: bool = True) -> Path:
    dev = base / port
    dev.mkdir(parents=True, exist_ok=True)
    (dev / "idVendor").write_text("0e8d", encoding="utf-8")
    (dev / "idProduct").write_text(pid, encoding="utf-8")
    if authorized_file:
        (dev / "authorized").write_text("1", encoding="utf-8")
    else:
        # 目录形态：open(..., "w") 必失败（root 亦同）——稳定触发回落路径
        (dev / "authorized").mkdir()
    return dev


# ── _set_authorized：直写 / 回落 / 降级 ────────────────────────────────────


def test_direct_write_wins(tmp_path):
    dev = _make_port(tmp_path, "1-7.4.4")
    ok, how = ff._set_authorized("1-7.4.4", "0", str(tmp_path))
    assert (ok, how) == (True, "direct")
    assert (dev / "authorized").read_text(encoding="utf-8") == "0"


def test_fallback_uses_wrapper_argv(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-7.4.4", authorized_file=False)
    rec = _Recorder(rc=0)
    monkeypatch.setattr(ff.subprocess, "run", rec)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: True)

    ok, how = ff._set_authorized("1-7.4.4", "0", str(tmp_path))
    assert (ok, how) == (True, "wrapper")
    assert rec.calls == [[
        "sudo", "-n", ff._PRIV_WRAPPER, "usb-authorized",
        "--port", "1-7.4.4", "--value", "0",
    ]]


def test_restore_uses_wrapper_value_one(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-8", authorized_file=False)
    rec = _Recorder(rc=0)
    monkeypatch.setattr(ff.subprocess, "run", rec)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: True)

    ok, how = ff._set_authorized("1-8", "1", str(tmp_path))
    assert (ok, how) == (True, "wrapper")
    assert rec.calls[0][-2:] == ["--value", "1"]


def test_no_priv_face_when_wrapper_unusable(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-7.4.4", authorized_file=False)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: False)
    assert ff._set_authorized("1-7.4.4", "0", str(tmp_path)) == \
        (False, "no-priv-face")


def test_wrapper_nonzero_marks_not_ok(tmp_path, monkeypatch):
    """wrapper 跑了但 rc≠0 → (False, "wrapper")——与 v1.3.15 的 sudo 口径平行。"""
    _make_port(tmp_path, "1-7.4.4", authorized_file=False)
    monkeypatch.setattr(ff.subprocess, "run", _Recorder(rc=2))
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: True)
    assert ff._set_authorized("1-7.4.4", "0", str(tmp_path)) == (False, "wrapper")


def test_wrapper_exception_reports_wrapper_failed(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-7.4.4", authorized_file=False)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: True)

    def boom(argv, **kwargs):
        raise OSError("exec failed")

    monkeypatch.setattr(ff.subprocess, "run", boom)
    assert ff._set_authorized("1-7.4.4", "0", str(tmp_path)) == \
        (False, "wrapper-failed")


# ── 能力探针：argv 形态 + 进程内缓存 ───────────────────────────────────────


def test_probe_argv_and_cache(monkeypatch):
    rec = _Recorder(rc=0)
    monkeypatch.setattr(ff.subprocess, "run", rec)

    assert ff._priv_wrapper_usable() is True
    assert ff._priv_wrapper_usable() is True          # 第二次命中缓存
    assert rec.calls == [[
        "sudo", "-n", ff._PRIV_WRAPPER, "usb-authorized", "--help",
    ]]


def test_probe_nonzero_is_false(monkeypatch):
    monkeypatch.setattr(ff.subprocess, "run", _Recorder(rc=2))
    assert ff._priv_wrapper_usable() is False


def test_probe_oserror_is_false(monkeypatch):
    def boom(argv, **kwargs):
        raise FileNotFoundError("sudo not found")

    monkeypatch.setattr(ff.subprocess, "run", boom)
    assert ff._priv_wrapper_usable() is False


# ── 门控集成：direct 与 wrapper 两条路径同场 ──────────────────────────────


def test_gate_other_mtk_mixes_direct_and_wrapper(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-7.3.3", pid="201c")                     # 目标
    _make_port(tmp_path, "1-7.4.4", pid="2000")                     # 幽灵：直写
    _make_port(tmp_path, "1-8", pid="2001", authorized_file=False)  # 回落 wrapper
    rec = _Recorder(rc=0)
    monkeypatch.setattr(ff.subprocess, "run", rec)
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: True)

    report = ff._gate_other_mtk("1-7.3.3", str(tmp_path))
    assert set(report["hidden"]) == {"1-7.4.4", "1-8"}
    assert report["errors"] == {}
    assert rec.calls == [[
        "sudo", "-n", ff._PRIV_WRAPPER, "usb-authorized",
        "--port", "1-8", "--value", "0",
    ]]


def test_gate_degrades_without_wrapper_and_records_reason(tmp_path, monkeypatch):
    _make_port(tmp_path, "1-7.3.3", pid="201c")
    _make_port(tmp_path, "1-8", pid="2001", authorized_file=False)
    monkeypatch.setattr(ff, "_is_linux", lambda: True)
    monkeypatch.setattr(ff, "_priv_wrapper_usable", lambda: False)

    report = ff._gate_other_mtk("1-7.3.3", str(tmp_path))
    assert report["hidden"] == []
    assert report["errors"]["1-8"] == "authorize-0 failed via no-priv-face"
    assert "cannot write authorized" in report["skipped_reason"]


# ── 源码级：不存在任意命令面 ───────────────────────────────────────────────


def test_no_shell_command_face_in_source():
    src = (_SCRIPT_DIR / "flash_firmware.py").read_text(encoding="utf-8")
    code = src.split('"""', 2)[2]          # 跳过模块 docstring
    assert '"sh", "-c"' not in src
    assert "_sudo_available" not in code
    assert not hasattr(ff, "_sudo_available")
    assert ff._PRIV_WRAPPER == "/usr/local/sbin/stp-agent-priv"
