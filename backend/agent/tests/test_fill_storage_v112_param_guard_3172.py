"""#3172（#3463 G3）：fill_storage v1.1.2 的 `fill_path` 白名单 + `_adb.py` 宽容解码。

v1.1.1 把 plan 参数原样插进设备端 root shell（`rm -f` / `dd of=` / `du -sk`）：
`/data/local/tmp/*` 会清掉整个目录、`" ; rm -rf /system ; "` 拼任意命令、带空格路径
静默重定向。判据 port 自 #3107 的 clear_recents.validated_dump_path（§3 G3）。
F2（#3069 形态）：`_adb.py` 的 `text=True` 严格解码同批收口（bytes + 宽容解码）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "fill_storage"

#: 生产实测的坏字节形态（#3069）：0xf9 在 UTF-8 里从不作首字节。
BROKEN_DEVICE_OUTPUT = b"\x02\xf9 INSTRUMENT_STATUS: class=com.transsion\n"


def _load(tag: str):
    d = _SCRIPTS  # ADR-0051 Phase 3：族树即最新版本（tag 只作模块名标签）
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"fill_storage_{tag}", d / "fill_storage.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod, adb_mod


@pytest.fixture()
def fill(monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    monkeypatch.delenv("STP_STEP_PARAMS", raising=False)
    monkeypatch.delenv("STP_ADB_PATH", raising=False)
    return _load("fs_v112")


class _Proc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def _fake_df_sequence(issued: list[str]):
    """df 首读 used=10000/total=100000（需填 50%），之后回读 60000（达标）；du 见 0 字节。"""
    calls = {"df": 0}

    def run(command: str, timeout: int = 30) -> _Proc:
        issued.append(command)
        if command.startswith("df /data"):
            calls["df"] += 1
            used = 10_000 if calls["df"] == 1 else 60_000
            return _Proc(stdout=(
                "Filesystem 1K-blocks Used Available Use% Mounted on\n"
                f"/dev/block/data 100000 {used} 40000 40% /data\n"
            ))
        if command.startswith("du -sk"):
            return _Proc(stdout="0\t/data/local/tmp/fill.bin\n")
        return _Proc(stdout="")

    return run


@pytest.mark.parametrize(
    "bad",
    [
        "/data/local/tmp/*",                    # 通配 ⇒ rm -f 清整个目录
        " ; rm -rf /system ; ",                 # 拼接任意 root 命令
        "/data/local/tmp/a b.bin",              # 带空格 ⇒ 静默重定向
        "/data/local/tmp/..",                   # 上跳
        "/data/local/tmp/.",
        "/data/local/tmp/../../etc/passwd",
        "/",                                    # rm -f /
        "/system/bin/fill.bin",                 # 白名单前缀之外
        "fill.bin",                             # 相对路径
        "/data/local/tmp/" + "a" * 65,          # 超长（>64 名段）
        42,
    ],
)
def test_invalid_fill_path_rejected(fill, bad):
    mod, _ = fill
    with pytest.raises(ValueError):
        mod.validated_fill_path(bad)


@pytest.mark.parametrize(
    "good",
    [
        None,
        "/data/local/tmp/fill.bin",
        "/data/local/tmp/a-b_c.1.bin",
        "/data/local/tmp/" + "a" * 64,          # 恰在白名单边界
    ],
)
def test_valid_fill_path_accepted(fill, good):
    mod, _ = fill
    assert mod.validated_fill_path(good).startswith("/data/local/tmp/")


def test_empty_fill_path_explicitly_falls_back(fill):
    """#3172 附带缺陷：键存在但为空串必须显式回退默认路径（v1.1.1 不回退 ⇒ `rm -f` 裸奔）。"""
    mod, _ = fill
    assert mod.validated_fill_path("") == "/data/local/tmp/fill.bin"
    assert mod.validated_fill_path("  ") == "/data/local/tmp/fill.bin"


@pytest.mark.parametrize("bad", ["/data/local/tmp/*", " ; rm -rf /system ; "])
def test_invalid_fill_path_fails_step_without_issuing_commands(fill, monkeypatch, capsys, bad):
    """§4 G3：非法值整步转红，且**不得**下发任何含该值的命令（宁可红不把破坏面交给参数）。"""
    mod, _ = fill
    issued: list[str] = []
    monkeypatch.setattr(
        mod, "adb_shell_quiet", lambda command, timeout=30: issued.append(command) or _Proc()
    )
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"fill_path": bad}))
    mod.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is False
    assert "fill_path" in payload["error_message"]
    assert issued == []


@pytest.mark.parametrize(
    ("param", "expected_path"),
    [
        ("", "/data/local/tmp/fill.bin"),                     # 空串回退默认
        ("/data/local/tmp/stp_a.bin", "/data/local/tmp/stp_a.bin"),  # 合法值原样使用
    ],
)
def test_step_uses_validated_path_in_commands(fill, monkeypatch, capsys, param, expected_path):
    mod, _ = fill
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell_quiet", _fake_df_sequence(issued))
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"fill_path": param}))
    mod.main()
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["success"] is True
    assert f"du -sk {expected_path}" in issued
    assert any(f"of={expected_path}" in c for c in issued), issued


def _write_fake_adb(tmp_path: Path, stdout_bytes: bytes) -> str:
    script = tmp_path / "fake_adb.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        f"import sys\nsys.stdout.buffer.write({stdout_bytes!r})\n",
        encoding="utf-8",
    )
    os.chmod(script, 0o755)
    return str(script)


def test_adb_helper_tolerates_broken_device_bytes(fill, tmp_path, monkeypatch):
    """F2（#3069 形态）反例：坏字节严格解码必抛；改宽容解码后不得炸掉 helper。"""
    _, adb_mod = fill
    with pytest.raises(UnicodeDecodeError):
        BROKEN_DEVICE_OUTPUT.decode("utf-8")  # 自证：这就是 text=True 的崩溃形态
    fake = _write_fake_adb(tmp_path, BROKEN_DEVICE_OUTPUT)
    monkeypatch.setenv("STP_ADB_PATH", fake)
    out = adb_mod.adb_shell("whatever")
    assert "\ufffd" in out and "INSTRUMENT_STATUS" in out
    res = adb_mod.adb_shell_quiet("whatever")
    assert isinstance(res.stdout, str) and isinstance(res.stderr, str)
    assert "\ufffd" in res.stdout


def test_adb_helper_has_no_strict_decode_call_form():
    src = (_SCRIPTS / "_adb.py").read_text(encoding="utf-8")
    assert "text=True," not in src
    assert "text=True)" not in src
