"""clear_recents v1.0.6（#3171 / #3463 G5）：三处「假绿」收口。

缺陷（v1.0.5）：#2976/#3104 修的是 dump 读取点，假绿面挪到了按键注入与判据自洽——
1. `_open_overview` 的 `input keyevent 187` 走只回 stdout 的 `adb_shell`，rc 被丢弃；
2. `already_clear` 只凭 `app==0 + 无清除按钮` 判成功，无「确在 Overview」正面证据
   ——锁屏 / 桌面 dump 同样是 0 卡无按钮 ⇒ 什么都没测就绿；
3. 快照计数 `if snaps:` 信任判据与过滤判据（`desc.strip()`）不一致——AOSP /
   Launcher3 上 desc 常挂卡片节点而非 snapshot ImageView ⇒ snaps 全空串 ⇒
   有卡屏上数出 0 ⇒ 绿。

v1.0.6 锁定六件事：

1. 锁屏 / 桌面 dump（0 卡、无按钮、无概览容器）判**非** `already_clear`，
   按瞬时读失败消耗尝试后转红，绝不落绿；
2. 正面证据正向控制：空概览有容器 id ⇒ 仍绿；ZTE 残留主屏幕卡（raw>0）⇒ 仍绿
   （v1.0.1 语义不回退）；
3. desc 全为空时快照计数回落到节点计数（正向控制：desc 可用时仍走快照路径）；
4. 有卡 + desc 全空的屏会被真的上滑清理，而不是判 already_clear；
5. `input keyevent 187` rc≠0 显式判失败（消耗尝试、转红保留证据），且失败后
   不再走到 dump；
6. `_adb` 设备输出 bytes 采集 + 宽松 UTF-8 解码（#3069 同形）：非 UTF-8 字节
   替换为 U+FFFD，不再炸 UnicodeDecodeError。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "clear_recents"


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.args = []


def _load_module(name: str, tag: str):
    spec = importlib.util.spec_from_file_location(f"{name}_{tag}", _SCRIPTS / name)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_v106():
    """加载族树 _adb + clear_recents（`from _adb import` 是导入期直绑，打桩必须
    落在脚本模块自己的名字上；ADR-0051 Phase 3 起族树即最新版本）。"""
    adb_mod = _load_module("_adb.py", "cr_v106_adb")
    sys.modules["_adb"] = adb_mod
    try:
        return _load_module("clear_recents.py", "cr_v106")
    finally:
        sys.modules.pop("_adb", None)


# ---------------------------------------------------------------------------
# fixtures：锁屏 / 桌面（0 卡、无按钮、无概览容器）vs 真概览
# ---------------------------------------------------------------------------

_LOCKSCREEN_DUMP = (
    '<hierarchy rotation="0">'
    '<node package="com.android.systemui" '
    'resource-id="com.android.systemui:id/keyguard_status_bar" bounds="[0,0][1080,80]"/>'
    '<node package="com.android.systemui" '
    'resource-id="com.android.systemui:id/keyguard_bottom_area" bounds="[0,2000][1080,2340]"/>'
    "</hierarchy>"
)

_DESKTOP_DUMP = (
    '<hierarchy rotation="0">'
    '<node package="com.zte.mifavor.launcher" '
    'resource-id="com.zte.mifavor.launcher:id/drag_layer" bounds="[0,0][1080,2340]"/>'
    '<node package="com.zte.mifavor.launcher" '
    'resource-id="com.zte.mifavor.launcher:id/workspace" bounds="[0,80][1080,2200]"/>'
    '<node package="com.zte.mifavor.launcher" '
    'resource-id="com.zte.mifavor.launcher:id/hotseat" bounds="[0,2200][1080,2340]"/>'
    "</hierarchy>"
)

# 空概览：0 卡、无按钮，但概览容器 id 在场 —— 正面证据成立。
_EMPTY_OVERVIEW_DUMP = (
    '<hierarchy rotation="0">'
    '<node package="com.zte.mifavor.launcher" '
    'resource-id="com.zte.mifavor.launcher:id/recents_view" bounds="[0,0][1080,2340]"/>'
    "</hierarchy>"
)

# ZTE 清空后的残留主屏幕卡：app 卡为 0 但 raw>0 —— raw 即正面证据（v1.0.1 语义）。
_HOME_CARD_ONLY_DUMP = (
    '<hierarchy rotation="0">'
    '<node package="com.zte.mifavor.launcher" '
    'resource-id="com.zte.mifavor.launcher:id/task_view_single" '
    'content-desc="主屏幕" bounds="[100,500][900,1500]"/>'
    "</hierarchy>"
)

# AOSP/Launcher3 形态：desc 挂卡片节点，snapshot ImageView 的 content-desc 全为空串
# ——v1.0.5 在此屏数出 0（#3171 缺陷 3 的最小复现）。
_AOSP_EMPTY_DESC_CARDS = (
    '<hierarchy rotation="0">'
    '<node resource-id="com.google.android.apps.nexuslauncher:id/task_view" '
    'bounds="[100,500][900,1500]">'
    '<node resource-id="com.google.android.apps.nexuslauncher:id/snapshot" '
    'content-desc="" bounds="[100,500][900,1500]"/>'
    "</node>"
    '<node resource-id="com.google.android.apps.nexuslauncher:id/task_view" '
    'bounds="[100,1600][900,2300]">'
    '<node resource-id="com.google.android.apps.nexuslauncher:id/snapshot" '
    'content-desc="" bounds="[100,1600][900,2300]"/>'
    "</node>"
    "</hierarchy>"
)

_EMPTY_HIERARCHY = '<hierarchy rotation="0"></hierarchy>'


def _run(monkeypatch, capsys, mod, *, cats: list[str], keyevent_rc: int = 0,
         keyevent_stderr: str = "", step_params: dict | None = None):
    """`cat` 按顺序消费 `cats`（耗尽后回空层级）；`uiautomator dump` 恒 rc=0；
    `input keyevent 187` 的 rc 由 `keyevent_rc` 注入。返回 (payload, issued)。"""
    issued: list[str] = []
    queue = list(cats)

    def fake_shell(command: str, timeout: int = 30) -> str:
        issued.append(command)
        return ""

    def fake_quiet(command: str, timeout: int = 30) -> _Proc:
        issued.append(command)
        if command.startswith("input keyevent 187"):
            if keyevent_rc != 0:
                return _Proc(keyevent_rc, stderr=keyevent_stderr)
            return _Proc(0)
        if "uiautomator dump" in command:
            return _Proc(0)
        if command.startswith("cat "):
            return _Proc(0, stdout=queue.pop(0) if queue else _EMPTY_HIERARCHY)
        return _Proc(0)

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    if step_params is not None:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(step_params))
    mod.main()
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "main() 未产出 stdout JSON"
    return json.loads(out[-1]), issued


# ---------------------------------------------------------------------------
# 缺陷 1/2：already_clear 必须有「站在 Overview」的正面证据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dump_xml", [_LOCKSCREEN_DUMP, _DESKTOP_DUMP], ids=["lockscreen", "desktop"])
def test_lockscreen_or_desktop_dump_is_not_already_clear(monkeypatch, capsys, dump_xml):
    """AC1：0 卡 + 无按钮 + 无概览容器 ⇒ 判非 already_clear，按读失败转红。"""
    mod = _load_v106()
    payload, _ = _run(monkeypatch, capsys, mod, cats=[dump_xml], step_params={"max_attempts": 1})
    assert payload["success"] is False, "锁屏/桌面 dump 不得判 already_clear（#3171 假绿）"
    m = payload["metrics"]
    assert m["already_clear"] is False
    assert m["ui_read_failed"] is True
    assert m["tasks_after"] is None, "无正面证据时不得写出未测量的 tasks_after"
    assert "无 Overview 正面证据" in m["read_errors"][0]


def test_overview_evidence_matrix():
    """证据判据本身：锁屏/桌面无证据；空概览容器与 raw 卡有证据。"""
    mod = _load_v106()
    assert mod._has_overview_evidence(_LOCKSCREEN_DUMP, 0) is False
    assert mod._has_overview_evidence(_DESKTOP_DUMP, 0) is False
    assert mod._has_overview_evidence(_EMPTY_OVERVIEW_DUMP, 0) is True
    assert mod._has_overview_evidence(_EMPTY_HIERARCHY, 1) is True


def test_empty_overview_with_container_still_green(monkeypatch, capsys):
    """正向控制：空概览有容器 id ⇒ 仍绿（不因收紧判据而误伤真概览）。"""
    mod = _load_v106()
    payload, _ = _run(monkeypatch, capsys, mod, cats=[_EMPTY_OVERVIEW_DUMP],
                      step_params={"max_attempts": 1})
    assert payload["success"] is True
    m = payload["metrics"]
    assert m["already_clear"] is True
    assert m["tasks_after"] == 0
    assert m["task_cards_raw_after"] == 0


def test_raw_home_card_counts_as_evidence_still_green(monkeypatch, capsys):
    """正向控制：ZTE 残留主屏幕卡（raw>0）⇒ 仍判 already_clear（v1.0.1 语义不回退）。"""
    mod = _load_v106()
    payload, _ = _run(monkeypatch, capsys, mod, cats=[_HOME_CARD_ONLY_DUMP],
                      step_params={"max_attempts": 1})
    assert payload["success"] is True
    m = payload["metrics"]
    assert m["already_clear"] is True
    assert m["tasks_after"] == 0
    assert m["task_cards_raw_after"] == 1


# ---------------------------------------------------------------------------
# 缺陷 3：desc 全为空时快照计数回落节点计数
# ---------------------------------------------------------------------------

def test_snapshot_desc_all_empty_falls_back_to_node_counting():
    """AC3：snaps 全空串 ⇒ 回落节点计数（v1.0.5 在同一输入返回 0）。"""
    mod = _load_v106()
    assert mod._count_app_tasks(_AOSP_EMPTY_DESC_CARDS) == 2
    assert mod._count_tasks(_AOSP_EMPTY_DESC_CARDS) == 2


def test_snapshot_desc_present_still_uses_snapshot_path():
    """正向控制：desc 可用时仍走快照路径（home 卡排除语义不变）。"""
    mod = _load_v106()
    zte_two = (
        '<hierarchy rotation="0">'
        '<node resource-id="com.zte.mifavor.launcher:id/snapshot" '
        'content-desc="Settings" bounds="[100,500][900,1500]"/>'
        '<node resource-id="com.zte.mifavor.launcher:id/snapshot" '
        'content-desc="主屏幕" bounds="[100,1600][900,2300]"/>'
        "</hierarchy>"
    )
    assert mod._count_app_tasks(zte_two) == 1


def test_aosp_empty_desc_cards_are_counted_and_swiped(monkeypatch, capsys):
    """AC4：有卡 + desc 全空的屏被真的上滑清理，而不是判 already_clear。"""
    mod = _load_v106()
    payload, _ = _run(
        monkeypatch, capsys, mod,
        cats=[_AOSP_EMPTY_DESC_CARDS, _EMPTY_OVERVIEW_DUMP],
        step_params={"max_attempts": 1},
    )
    assert payload["success"] is True
    m = payload["metrics"]
    assert m["tasks_before"] == 2, "desc 全空 ⇒ 回落节点计数；v1.0.5 此处为 0（假绿）"
    assert m["swipes"] == 2, "残留卡应被上滑关闭，而不是无视"
    assert m["tasks_after"] == 0
    assert m["already_clear"] is False


# ---------------------------------------------------------------------------
# 缺陷 1：keyevent 187 的 rc 不再被丢弃
# ---------------------------------------------------------------------------

def test_open_overview_surfaces_rc(monkeypatch):
    """AC5（单元）：rc≠0 时 `_open_overview` 返回携带 rc 与 stderr 的失败原因。"""
    mod = _load_v106()
    monkeypatch.setattr(
        mod, "adb_shell_quiet",
        lambda command, timeout=30: _Proc(1, stderr="error: device offline"),
    )
    err = mod._open_overview()
    assert err is not None
    assert "rc=1" in err
    assert "device offline" in err


def test_keyevent_rc_nonzero_fails_step(monkeypatch, capsys):
    """AC5：keyevent rc≠0 按读失败处理——消耗尝试后转红、保留证据、不走 dump。"""
    mod = _load_v106()
    payload, issued = _run(
        monkeypatch, capsys, mod,
        cats=[_EMPTY_OVERVIEW_DUMP],
        keyevent_rc=1, keyevent_stderr="error: device offline",
        step_params={"max_attempts": 2},
    )
    assert payload["success"] is False
    m = payload["metrics"]
    assert m["ui_read_failed"] is True
    assert m["attempts"] == 2, "重试必须真的发生"
    assert len(m["read_errors"]) == 2
    assert all("input keyevent 187 rc=1" in r for r in m["read_errors"])
    assert "device offline" in payload["error_message"]
    assert not any("uiautomator dump" in c for c in issued), "keyevent 失败后不得走到 dump"


# ---------------------------------------------------------------------------
# F2：设备输出宽松解码（#3069 同形）
# ---------------------------------------------------------------------------

def test_decode_device_output_is_lenient():
    adb_mod = _load_module("_adb.py", "cr_v106_adb_decode")
    assert adb_mod.decode_device_output(b"ok") == "ok"
    assert adb_mod.decode_device_output(None) == ""
    out = adb_mod.decode_device_output(b"bad\xf9bytes")
    assert "\ufffd" in out
    assert "bad" in out and "bytes" in out


def test_adb_shell_lenient_on_non_utf8(monkeypatch):
    """F2：非 UTF-8 设备输出不再炸 UnicodeDecodeError（text=True 形态的回归面）。"""
    adb_mod = _load_module("_adb.py", "cr_v106_adb_lenient")

    class _BytesProc:
        returncode = 0
        stdout = b"\xf9\x81 dump ok"
        stderr = b""
        args = ["adb", "shell"]

    monkeypatch.setattr(adb_mod.subprocess, "run", lambda *a, **k: _BytesProc())
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    out = adb_mod.adb_shell("uiautomator dump /data/local/tmp/x.xml")
    assert "dump ok" in out
    assert "\ufffd" in out
