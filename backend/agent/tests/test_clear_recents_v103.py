"""clear_recents v1.0.3（#2976）：UI 层级读失败必须转红，不得伪造 `tasks_after=0`。

缺陷（v1.0.0–v1.0.2）：`_dump_ui` 丢 rc——`uiautomator dump` 失败后 `cat`
拿不到文件（错误在 stderr，`adb_shell` 只回 stdout）返回空串，计数 0 命中
「本来就没有任务卡」提前成功分支：**一次 adb 读失败被记成绿色成功**，
且写出从未测量的 `tasks_after: 0`。连带 `require_tasks=true` 的守卫挂在
尝试耗尽之后，无任务场景永远走提前成功分支——形同虚设。

v1.0.3 锁定四条 + v1.0.2 对照锚点（旧版本不可变，只读断言）：
1. dump rc≠0 / cat rc≠0 / cat 空内容 三态全部 `success=False` 且
   `metrics.ui_read_failed=true`，`tasks_after` 保持 None（不编造）；
2. 真·无任务卡（成功读取的空层级）仍判 `already_clear` 绿色，且此时
   `tasks_after=0` 是测出来的；
3. `require_tasks=true` + 成功读取且 0 卡 → 按 docstring 语义判失败；
4. 点击后的复核 dump 失败同样转红（v1.0.2 复核读失败会算成 app_after=0 假绿）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "clear_recents"

_EMPTY_HIERARCHY = '<hierarchy rotation="0"></hierarchy>'
_WITH_APP_TASK = (
    '<hierarchy rotation="0">'
    '<node resource-id="com.zte.mifavor.launcher:id/remove_all_button_layout" '
    'content-desc="全部清除" clickable="true" bounds="[540,2200][810,2320]"/>'
    '<node resource-id="com.zte.mifavor.launcher:id/task_view_single" '
    'content-desc="Settings" bounds="[100,500][900,1500]"/>'
    "</hierarchy>"
)


def _load_version(version: str, tag: str):
    """加载某版本的 _adb + clear_recents；`from _adb import` 是导入期直绑，
    打桩必须落在脚本模块自己的名字上（同 monkey_setup 测试的教训）。"""
    d = _SCRIPTS / version
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"clear_recents_{tag}", d / "clear_recents.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _install_stubs(monkeypatch, mod, *, dump_proc, cat_proc):
    """按命令子串派发假 adb；返回调用记录以便断言 rm/计数行为。"""
    calls: list[str] = []

    def fake_shell(command: str, timeout: int = 30) -> str:
        calls.append(command)
        return ""

    def fake_shell_quiet(command: str, timeout: int = 30) -> _Proc:
        calls.append(command)
        if "uiautomator dump" in command:
            return dump_proc
        if command.startswith("cat "):
            return cat_proc
        return _Proc(0)

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_shell_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    return calls


def _run_main(monkeypatch, mod, *, dump_proc, cat_proc, step_params: dict | None = None):
    calls = _install_stubs(monkeypatch, mod, dump_proc=dump_proc, cat_proc=cat_proc)
    if step_params is not None:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(step_params))
    mod.main()
    return calls


def _payload(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "main() 未产出 stdout JSON"
    return json.loads(out[-1])


@pytest.mark.parametrize(
    "dump_proc,cat_proc,label",
    [
        (_Proc(1, stderr="ERROR: could not get idle state."), _Proc(1, stderr="No such file"), "dump rc≠0"),
        (_Proc(0), _Proc(1, stderr="cat: /data/local/tmp/x.xml: No such file"), "cat rc≠0"),
        (_Proc(0), _Proc(0, stdout=""), "cat 空输出"),
    ],
    ids=["dump-fail", "cat-fail", "cat-empty"],
)
def test_read_failure_turns_red_not_fake_success(monkeypatch, capsys, dump_proc, cat_proc, label):
    """AC1/AC2：读失败三态不得进「本来就没有任务卡」分支、不得编造 tasks_after。"""
    mod = _load_version("v1.0.3", "cr_v103")
    _run_main(monkeypatch, mod, dump_proc=dump_proc, cat_proc=cat_proc)
    payload = _payload(capsys)
    assert payload["success"] is False, f"{label} 在 v1.0.3 仍被判绿"
    m = payload["metrics"]
    assert m["ui_read_failed"] is True
    assert m["already_clear"] is False
    assert m["tasks_after"] is None, f"{label} 仍写出未测量的 tasks_after={m['tasks_after']!r}"
    assert m["tasks_before"] is None
    assert "UI 层级读取失败" in payload["error_message"]


def test_true_empty_overview_still_green_and_measured(monkeypatch, capsys):
    """成功读取的空层级：already_clear 绿色成立，且 tasks_after=0 出自测量。"""
    mod = _load_version("v1.0.3", "cr_v103")
    _run_main(monkeypatch, mod, dump_proc=_Proc(0), cat_proc=_Proc(0, stdout=_EMPTY_HIERARCHY))
    payload = _payload(capsys)
    assert payload["success"] is True
    m = payload["metrics"]
    assert m["already_clear"] is True
    assert m["ui_read_failed"] is False
    assert m["tasks_before"] == 0 and m["tasks_after"] == 0


def test_require_tasks_now_reachable(monkeypatch, capsys):
    """AC3：require_tasks=true + 无最近任务（成功读取）→ 按 docstring 判失败。"""
    mod = _load_version("v1.0.3", "cr_v103")
    _run_main(
        monkeypatch, mod,
        dump_proc=_Proc(0), cat_proc=_Proc(0, stdout=_EMPTY_HIERARCHY),
        step_params={"require_tasks": True},
    )
    payload = _payload(capsys)
    assert payload["success"] is False
    assert payload["error_message"] == "no recent tasks to clear"
    assert payload["metrics"]["already_clear"] is False


def test_verify_dump_failure_not_fake_green(monkeypatch, capsys):
    """AC2 复核位：点击成功但复核 dump 读失败 → 转红，不得写 tasks_after=0。"""
    mod = _load_version("v1.0.3", "cr_v103")
    seq = [
        _Proc(0, stdout=_WITH_APP_TASK),   # 首个成功 dump：有卡 + 有清除按钮
        _Proc(1, stderr="ERROR: could not get idle state."),  # 点击后复核失败
    ]
    calls: list[str] = []

    def fake_shell(command: str, timeout: int = 30) -> str:
        calls.append(command)
        return ""

    def fake_shell_quiet(command: str, timeout: int = 30) -> _Proc:
        calls.append(command)
        if "uiautomator dump" in command:
            return _Proc(0)
        if command.startswith("cat "):
            return seq.pop(0)
        return _Proc(0)

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_shell_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"max_attempts": 1}))
    mod.main()
    payload = _payload(capsys)
    assert payload["success"] is False
    m = payload["metrics"]
    assert m["ui_read_failed"] is True
    assert m["tasks_after"] is None, "复核读失败仍被写成 app_after=0 假绿"
    assert m["tapped"] is True


def test_v102_anchor_reproduces_fake_green(monkeypatch, capsys):
    """对照锚点：同一读失败桩下 v1.0.2 判绿且编造 tasks_after=0——差异确由 v1.0.3 引入。"""
    mod = _load_version("v1.0.2", "cr_v102_anchor")

    def fake_shell(command: str, timeout: int = 30) -> str:
        return ""

    def fake_shell_quiet(command: str, timeout: int = 30) -> _Proc:
        return _Proc(1, stderr="ERROR: could not get idle state.")

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_shell_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    mod.main()
    payload = _payload(capsys)
    assert payload["success"] is True, "锚点失效：v1.0.2 不再复现假绿（本文件前提需重审）"
    assert payload["metrics"]["already_clear"] is True
    assert payload["metrics"]["tasks_after"] == 0  # 编造值：dump 从未成功
