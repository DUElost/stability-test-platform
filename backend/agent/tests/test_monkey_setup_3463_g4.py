"""monkey_setup v2.3.12（#3173，#3463 批次 G4）：心跳 seq 进程级化 + 残余参数注入面。

三个修复面（#3463 §3 G4）：
  - F4：`_make_progress` 的 seq 此前是 per-closure 闭包计数（``state={"seq": 0}``），
    init 已发到 seq N 后 push/fill 各闭包从 1 重启——引擎停滞钟只认进程内单调
    seq（``seq > last_seq``），后阶段心跳被静默丢弃。现取 `_adb._next_progress_seq`
    （进程级 + 锁）。
  - F3：`fill_path` / `push.files[].remote` / `chmod` / `pm uninstall` 包名 /
    `setprop` 键值五处插值先校验再进 root shell，非法值整步转红。
  - F2：`_adb.py` 收字节 + 宽容解码（gpu_setup 先例 port），坏字节不再炸
    UnicodeDecodeError。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"
_TEMPLATES = Path(__file__).resolve().parents[3] / "backend" / "schemas" / "pipeline_templates"


def _load(tag: str):
    """按文件加载族树（ADR-0051 Phase 3：族树即最新版本）。

    每次调用得到**独立模块实例**——进程级 seq 计数器从 0 重新开始，精确值断言
    因此与测试执行顺序无关（同 test_monkey_setup_v2311 的加载姿势）。
    """
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", _SCRIPTS / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"monkey_setup_{tag}", _SCRIPTS / "monkey_setup.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


def _load_adb(tag: str):
    """单独加载 `_adb.py` 模块实例（decode_device_output 等助手直测用）。"""
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", _SCRIPTS / "_adb.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stderr_stamps(err: str) -> list[dict]:
    return [
        json.loads(line[len("PROGRESS "):])
        for line in err.splitlines() if line.startswith("PROGRESS ")
    ]


# ── F4：多闭包交替发送时 seq 严格单调（引擎 `seq > last_seq` 判据） ──────

def test_seq_monotonic_across_interleaved_closures(capsys):
    """init / push / fill 三闭包交替发送，seq 全局严格单调且互不重置。"""
    mod = _load("g4_seq_mono")
    init = mod._make_progress("init")
    push = mod._make_progress("push")
    fill = mod._make_progress("fill")
    init(phase="init")
    push(written_bytes=1)
    fill(written_bytes=2)
    push(written_bytes=3)
    init(phase="step:push:start")
    fill(written_bytes=4)
    err = capsys.readouterr().err
    seqs = [s["seq"] for s in _stderr_stamps(err)]
    # 每实例计数器从 0 起：6 次交替发送 ⇒ 1..6，任何闭包切换都不回退
    assert seqs == [1, 2, 3, 4, 5, 6], seqs


def test_two_closures_never_share_a_seq_value(capsys):
    """同一 seq 值不得出现两次——引擎按 `seq > last_seq` 丢弃非单调戳（#3173 现象）。"""
    mod = _load("g4_seq_unique")
    a = mod._make_progress("push")
    b = mod._make_progress("fill")
    for _ in range(3):
        a(written_bytes=1)
        b(written_bytes=2)
    err = capsys.readouterr().err
    seqs = [s["seq"] for s in _stderr_stamps(err)]
    assert len(seqs) == len(set(seqs)), seqs


def test_main_step_stamps_are_globally_monotonic(fake_adb, monkeypatch, capsys):
    """真实链路：main() 的 init / step:start / step:end 戳在进程级计数器下全局单调。"""
    mod = _load("g4_main")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "FAKESERIAL")
    monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
    monkeypatch.delenv("STP_WIFI_SSID", raising=False)
    monkeypatch.delenv("STP_WIFI_PASSWORD", raising=False)
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"steps": ["wifi"]}))
    mod.main()
    err = capsys.readouterr().err
    seqs = [s["seq"] for s in _stderr_stamps(err)]
    assert len(seqs) >= 3, seqs  # init + step:start + step:end 至少三枚
    assert all(b > a for a, b in zip(seqs, seqs[1:])), seqs


# ── F3：fill_path（port clear_recents validated_dump_path 判据） ─────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "/data/local/tmp/fill.bin"),   # 未传 ⇒ 默认
        ("", "/data/local/tmp/fill.bin"),     # 空串显式回落默认值
        ("   ", "/data/local/tmp/fill.bin"),
        ("/data/local/tmp/fill.bin", "/data/local/tmp/fill.bin"),
        ("/data/local/tmp/stress1.bin", "/data/local/tmp/stress1.bin"),
    ],
)
def test_valid_fill_path(raw, expected):
    mod = _load("g4_fp_good")
    assert mod.validated_fill_path(raw) == expected


@pytest.mark.parametrize(
    "bad",
    [
        " ; rm -rf /system ; ",
        "/data/local/tmp/*",
        "/data/local/tmp/../evil",
        "/data/local/tmp/.",
        "/data/local/tmp/..",
        "/etc/passwd",
        "relative.bin",
        "/data/local/tmp/" + "a" * 65,  # 超长（判据上限 64）
        "/data/local/tmp/a b",
        "/data/local/tmp/a;id",
    ],
)
def test_invalid_fill_path_rejected(bad):
    mod = _load("g4_fp_bad")
    with pytest.raises(ValueError):
        mod.validated_fill_path(bad)


# ── F3：push.files[].remote / chmod ─────────────────────────────────────

@pytest.mark.parametrize(
    "good",
    [
        "/sdcard/test_resources/app.apk",
        "/data/local/tmp/f.bin",
        "/data/vendor/logs/x",
    ],
)
def test_valid_remote_path(good):
    mod = _load("g4_rp_good")
    assert mod.validated_remote_path(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "relative/x",
        "/sdcard",
        "/data/../etc",
        "/sdcard/x; rm -rf /",
        "/sdcard/a b",
        "/system/x",
        "/sdcard/x/../../y",
    ],
)
def test_invalid_remote_path_rejected(bad):
    mod = _load("g4_rp_bad")
    with pytest.raises(ValueError):
        mod.validated_remote_path(bad)


@pytest.mark.parametrize("good", ["644", "755", "777", "0755", "1777"])
def test_valid_chmod_mode(good):
    mod = _load("g4_cm_good")
    assert mod.validated_chmod_mode(good) == good


@pytest.mark.parametrize("bad", ["", "abc", "999", "77", "77777", "64 4", "chmod 777"])
def test_invalid_chmod_mode_rejected(bad):
    mod = _load("g4_cm_bad")
    with pytest.raises(ValueError):
        mod.validated_chmod_mode(bad)


# ── F3：pm uninstall 包名 / setprop 键 ──────────────────────────────────

@pytest.mark.parametrize("good", ["com.example.app", "a-b_c.d", "123"])
def test_valid_pkg_name(good):
    mod = _load("g4_pk_good")
    assert mod.validated_pkg_name(good, "uninstall_packages 包名") == good


@pytest.mark.parametrize("bad", ["", "com example", "x; rm -rf /", "x$(id)", "a/b"])
def test_invalid_pkg_name_rejected(bad):
    mod = _load("g4_pk_bad")
    with pytest.raises(ValueError):
        mod.validated_pkg_name(bad, "uninstall_packages 包名")


# ── F3：step 级反例——非法值整步转红，且不向设备发出任何命令 ─────────────

def test_step_clean_rejects_bad_pkg_without_adb_call(monkeypatch):
    mod = _load("g4_clean_pkg")
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    result = mod.step_clean("TESTSERIAL", {"uninstall_packages": ["x; rm -rf /"]})
    assert result["success"] is False
    assert issued == [], "非法包名不得进设备 shell"
    assert "包名" in result["error"]


def test_step_clean_rejects_bad_setprop_key_without_adb_call(monkeypatch):
    mod = _load("g4_clean_key")
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    result = mod.step_clean("TESTSERIAL", {"set_properties": {"ro.build.x;id": "1"}})
    assert result["success"] is False
    assert issued == []
    assert "set_properties 键" in result["error"]


def test_step_clean_quotes_setprop_value(monkeypatch):
    """合法键 + 含空格值：shlex.quote 保证仍是一个 setprop 参数（G3 判据）。"""
    mod = _load("g4_clean_quote")
    issued: list[str] = []
    monkeypatch.setattr(mod, "adb_shell", lambda command, timeout=30: issued.append(command) or "")
    result = mod.step_clean(
        "TESTSERIAL", {"set_properties": {"persist.stp.mode": "monkey stress"}}
    )
    assert result["success"] is True
    assert issued == ["setprop persist.stp.mode 'monkey stress'"]


def test_step_push_rejects_bad_chmod_without_any_push(monkeypatch):
    mod = _load("g4_push_chmod")
    pushes: list[tuple] = []
    monkeypatch.setattr(
        mod, "_push_or_timeout",
        lambda *a, **kw: pushes.append((a, kw)) or None,
    )
    result = mod.step_push(
        "TESTSERIAL",
        {"files": [{"local": "a.apk", "remote": "/sdcard/x/a.apk", "chmod": "abc"}]},
    )
    assert result["success"] is False
    assert pushes == [], "校验失败必须发生在任何 push 之前（整步转红）"
    assert "chmod" in result["error"]


def test_step_push_rejects_bad_remote_without_any_push(monkeypatch):
    mod = _load("g4_push_remote")
    pushes: list[tuple] = []
    monkeypatch.setattr(
        mod, "_push_or_timeout",
        lambda *a, **kw: pushes.append((a, kw)) or None,
    )
    result = mod.step_push(
        "TESTSERIAL",
        {"files": [{"local": "a.apk", "remote": "/system/x/a.apk"}]},
    )
    assert result["success"] is False
    assert pushes == []
    assert "remote" in result["error"]


def test_step_fill_rejects_bad_fill_path_without_dd(monkeypatch):
    mod = _load("g4_fill_path")
    dd_calls: list = []
    monkeypatch.setattr(mod, "_dd_with_progress", lambda *a, **kw: dd_calls.append(a))
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda command, timeout=10: _FakeDfProc())
    result = mod.step_fill("TESTSERIAL", {"fill_path": "/data/local/tmp/../evil"})
    assert result["success"] is False
    assert dd_calls == [], "非法 fill_path 不得触达 dd"
    assert "fill_path" in result["error"]


def test_step_fill_default_fill_path_still_works(monkeypatch):
    """默认值（未传 / 空串）必须继续工作——白名单不得拒绝既有合法形态。"""
    mod = _load("g4_fill_default")
    dd_calls: list = []
    monkeypatch.setattr(mod, "_dd_with_progress", lambda *a, **kw: dd_calls.append(a))
    monkeypatch.setattr(mod, "adb_shell_quiet", lambda command, timeout=10: _FakeDfProc())
    for cfg in ({}, {"fill_path": ""}):
        result = mod.step_fill("TESTSERIAL", cfg)
        assert result["success"] is True, (cfg, result)
    assert dd_calls[0][1] == "/data/local/tmp/fill.bin"


# ── F2：_adb.py 宽容解码（gpu_setup 先例 port） ─────────────────────────

def test_decode_device_output_replaces_bad_bytes():
    adb_mod = _load_adb("g4_decode")
    assert adb_mod.decode_device_output(b"ok\xf9line") == "ok\ufffdline"
    assert adb_mod.decode_device_output(None) == ""
    assert adb_mod.decode_device_output(b"") == ""


def test_adb_shell_quiet_survives_non_utf8_output(fake_adb, monkeypatch):
    """设备吐非 UTF-8 字节时 adb_shell_quiet 仍返回 str 字段（不抛 UnicodeDecodeError）。"""
    mod = _load("g4_decode_quiet")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "FAKESERIAL")
    monkeypatch.setenv("STP_ADB_PATH", str(fake_adb))
    proc = mod.adb_shell_quiet("echo raw", timeout=10)
    assert isinstance(proc.stdout, str)
    assert "\ufffd" in proc.stdout
    assert proc.returncode == 0


# ── 模板 pin = 2.3.12（#3463 §4 G4 验收项；跳过 2.3.11） ─────────────────

@pytest.mark.parametrize("template", ["monkey.json", "monkey_watcher_patrol.json"])
def test_template_pins_monkey_setup_2312(template):
    data = json.loads((_TEMPLATES / template).read_text(encoding="utf-8"))
    versions = [
        step.get("version")
        for step in _iter_steps(data)
        if step.get("action") == "script:monkey_setup"
    ]
    assert versions, template
    assert versions == ["2.3.12"], versions


def _iter_steps(obj):
    if isinstance(obj, dict):
        if "action" in obj:
            yield obj
        for value in obj.values():
            yield from _iter_steps(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_steps(item)


# ── fixtures ─────────────────────────────────────────────────────────────

class _FakeDfProc:
    """step_fill 里 `df /data` 的最小假 CompletedProcess（10% 已用，需要填充）。"""

    returncode = 0
    stdout = (
        "Filesystem 1K-blocks Used Available Use% Mounted on\n"
        "/data 1000000 100000 900000 10% /data\n"
    )
    stderr = ""


@pytest.fixture()
def fake_adb(tmp_path):
    """假 adb：`shell echo ...` 回显**带坏字节**的输出，供 F2 解码链路。"""
    script = tmp_path / "adb"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if 'shell' in args:\n"
        "    cmd = args[args.index('shell') + 1]\n"
        "    if cmd.startswith('echo '):\n"
        "        sys.stdout.buffer.write(cmd[5:].encode() + b'\\xf9\\n')\n"  # 故意坏字节
        "        sys.exit(0)\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script
