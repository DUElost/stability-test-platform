"""clear_recents v1.0.4（#3104）：读失败**先消耗一次尝试**，不再零重试即终结整步。

缺陷（v1.0.3）：#2976 把「读失败判成 success」修成「读失败一律转红」，方向正确，
但 abort 无条件且发生在重试循环内部 —— `max_attempts` 对「读失败」这一类失败
不可达：一次瞬时抖动就让整步红。而本脚本的目标故障形态（设备重启窗里
`uiautomator dump` 失败）恰恰是**瞬时**的，且模板/Plan 侧 `retry` 未设（默认 0），
引擎不会补重试 ⇒ 重试只能由脚本自己承担。

v1.0.4 锁定四件事（外加 v1.0.3 对照锚点）：

1. 首次读失败 + 第二次成功 ⇒ **绿**，且 `metrics.read_errors` 留下那一笔瞬时证据、
   `attempts=2`（重试真的发生了）；
2. 所有尝试都读失败 ⇒ 仍**红**，`ui_read_failed=true`、`tasks_after` 保持 None
   （不编造）、`read_errors` 记录每次原因 —— #2976 的判据一条都没放松；
3. `max_attempts=1` 时不吞重试：一次失败即红，`read_errors` 恰好 1 条；
4. 点击后的**复核位**读失败同样进入重试（不是只有首次读取才重试）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

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


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _load_version(version: str, tag: str):
    """加载某版本的 _adb + clear_recents（同 test_clear_recents_v103 的姿势：
    `from _adb import` 是导入期直绑，打桩必须落在脚本模块自己的名字上）。"""
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


def _run(monkeypatch, capsys, mod, *, cat_results: list[_Proc], step_params: dict | None = None):
    """`cat` 按顺序消费 `cat_results`（耗尽后回空层级 = 读到但无任务卡）；
    `uiautomator dump` 恒 rc=0 —— 读失败由 cat 侧注入，与 v1.0.3 测试同一姿势。"""
    cats = list(cat_results)
    calls: list[str] = []

    def fake_shell(command: str, timeout: int = 30) -> str:
        calls.append(command)
        return ""

    def fake_shell_quiet(command: str, timeout: int = 30) -> _Proc:
        calls.append(command)
        if "uiautomator dump" in command:
            return _Proc(0)
        if command.startswith("cat "):
            return cats.pop(0) if cats else _Proc(0, stdout=_EMPTY_HIERARCHY)
        return _Proc(0)

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_shell_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    if step_params is not None:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(step_params))
    mod.main()
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "main() 未产出 stdout JSON"
    return json.loads(out[-1]), calls


_CAT_FAIL = _Proc(1, stderr="cat: /data/local/tmp/x.xml: No such file")


def test_transient_read_failure_retries_then_green(monkeypatch, capsys):
    """AC1：首次读失败 + 第二次读到 ⇒ 绿；`read_errors` 留下瞬时证据、attempts=2。

    v1.0.3 在同一输入下判红（见本文件锚点用例）——这正是 #3104 要消掉的形态：
    一次抖动 = 整步失败。"""
    mod = _load_version("v1.0.4", "cr_v104")
    payload, _ = _run(monkeypatch, capsys, mod, cat_results=[_CAT_FAIL])
    assert payload["success"] is True, "瞬时读失败仍终结整步（#3104 未修）"
    m = payload["metrics"]
    assert m["ui_read_failed"] is False
    assert m["attempts"] == 2, "重试没有真的发生（attempts 应记录到第 2 次）"
    assert len(m["read_errors"]) == 1, "瞬时失败没有留证据"
    assert "attempt 1" in m["read_errors"][0]
    assert m["already_clear"] is True
    assert m["tasks_after"] == 0, "成功分支的 tasks_after 必须出自一次成功读取"


def test_all_attempts_read_failed_still_red(monkeypatch, capsys):
    """AC2：#2976 的判据不许放松 —— 每次尝试都读不到就转红，且不编造 tasks_after。"""
    mod = _load_version("v1.0.4", "cr_v104")
    payload, _ = _run(monkeypatch, capsys, mod, cat_results=[_CAT_FAIL, _CAT_FAIL, _CAT_FAIL])
    assert payload["success"] is False
    m = payload["metrics"]
    assert m["ui_read_failed"] is True
    assert m["tasks_after"] is None, "读失败仍写出未测量的 tasks_after"
    assert m["tasks_before"] is None
    assert len(m["read_errors"]) == 2, "默认 max_attempts=2，应有两条失败记录"
    assert "2 次尝试均未读到 UI 层级" in payload["error_message"]


def test_max_attempts_one_does_not_swallow_the_retry(monkeypatch, capsys):
    """AC3：`max_attempts=1` 时一次失败即红，不吞成「已重试」。"""
    mod = _load_version("v1.0.4", "cr_v104")
    payload, _ = _run(monkeypatch, capsys, mod, cat_results=[_CAT_FAIL], step_params={"max_attempts": 1})
    assert payload["success"] is False
    assert len(payload["metrics"]["read_errors"]) == 1
    assert payload["metrics"]["attempts"] == 1


def test_verify_position_read_failure_also_retries(monkeypatch, capsys):
    """AC4：点击后的复核位读失败进入重试（不是只有首次读取才重试）。"""
    mod = _load_version("v1.0.4", "cr_v104")
    payload, _ = _run(
        monkeypatch, capsys, mod,
        cat_results=[_Proc(0, stdout=_WITH_APP_TASK), _CAT_FAIL],  # 首次读到卡+按钮，复核失败
    )
    assert payload["success"] is True
    m = payload["metrics"]
    assert m["tapped"] is True, "首次尝试确实点了清除"
    assert len(m["read_errors"]) == 1, "复核位失败未进入重试"
    assert m["attempts"] == 2
    assert m["ui_read_failed"] is False


def test_v103_anchor_dies_on_the_same_transient(monkeypatch, capsys):
    """对照锚点：同一「瞬时读失败」输入下 v1.0.3 判红 —— 差异确由 v1.0.4 引入。"""
    mod = _load_version("v1.0.3", "cr_v103_anchor")
    payload, _ = _run(monkeypatch, capsys, mod, cat_results=[_CAT_FAIL])
    assert payload["success"] is False, "锚点失效：v1.0.3 不再复现「零重试即判红」（本文件前提需重审）"
    assert payload["metrics"]["ui_read_failed"] is True
    assert "read_errors" not in payload["metrics"], "v1.0.3 不应有 v1.0.4 的字段"
