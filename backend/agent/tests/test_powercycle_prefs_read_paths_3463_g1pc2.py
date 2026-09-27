"""#3463 §9 **G1-pc-2**：powercycle 三族 prefs 读路径全部 root 优先且区分 kind。

复核退回项（#3471 复核 B1–B4 / 方案 v1.2 §9.1）：F1 的范围是「六族内**所有** prefs
读路径」——#3471 只收口了 `_root_read_prefs`/`repair_prefs_ownership`/`set_stop_flags`，
还剩三条读路径把「读空」当作「不存在」或「值为 0」：

1. check/finish 的 `get_prefs_xml` 是 run-as-only（platform shared-uid 包恒拒 ⇒ 恒空），
   root 下 `start/resume/verify` 全部瞎读（B3/B4）；
2. `set_prefs(reset_count=false)` 把读空折叠成 `current_count=0` 并整写完整 prefs
   ——重启窗 adb 超时可把健康续跑计数覆盖为 0（B2）；
3. `resume_task`/`start_task` 读空即跳过 `auto_resume=true`/`running=true` 却照常
   启动服务（B3：表面恢复，reboot 后不再续跑）；
4. `_verify_stop_flags` 用 run-as-only 回读——root 写成功但 run-as 被拒 ⇒ 假失败（B4）。

同时钉住 §9.1 裁定一：`powercycle_check` 收取窗口的步骤语义**不变**——`set_stop_flags`
的 raise 由窗口既有重试语义承接（`collect_error` + 下周期重试 + 补偿 resume +
`success=true`），顶层反例用**真实** `_run → pause_task → set_stop_flags` 链验证。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_SCRIPTS = REPO_ROOT / "backend" / "agent" / "scripts"
_PREFS_PATH = "/data/data/com.tinno.autotesttool/shared_prefs/powercycle_runner.xml"
_PROBE_PREFIX = "if [ ! -f "
_ABSENT_SENTINEL = "__STP_PREFS_ABSENT__"

FAMILIES = ("powercycle_setup", "powercycle_check", "powercycle_finish")

_HEALTHY_XML = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <int name="test_times" value="100"/>\n'
    '    <int name="current_count" value="42"/>\n'
    '    <boolean name="auto_resume" value="true"/>\n'
    '    <boolean name="running" value="true"/>\n'
    "</map>\n"
)
_STOPPED_XML = (
    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n"
    "<map>\n"
    '    <boolean name="auto_resume" value="false"/>\n'
    '    <boolean name="running" value="false"/>\n'
    "</map>\n"
)
_RESET_CFG = {
    "test_times": 100, "mode": "reboot", "power_off_minutes": 1,
    "wait_seconds": 3, "tester": "tester", "auto_resume": True, "reset_count": False,
}


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


def _load_entry_with_lib(name: str, family: str):
    """加载入口脚本并捕获它实际绑定的 ``_lib`` 模块（`from _lib import` 的同源模块）。

    顶层反例需要真实链 ``_run → pause_task → set_stop_flags``：entry 绑定的函数
    对象其 globals 属于 sys.modules['_lib']，打桩必须落在**同一个**模块字典上。
    """
    d = _SCRIPTS / family
    sys.path.insert(0, str(d))
    try:
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, d / f"{family}.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        lib = sys.modules.get("_lib")
        assert lib is not None, "entry 未按预期经 sys.path 拉起 _lib"
        return mod, lib
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(d))


@pytest.fixture(scope="module", params=FAMILIES)
def lib(request):
    return _load(f"pcg1pc2_lib_{request.param}", f"{request.param}/_lib.py")


def _install_fake_adb(monkeypatch, mod, routes: list[tuple[str, tuple]]):
    """按命令子串派发假 adb；返回全部调用记录（对齐 v123/g1pc 夹具）。"""
    calls: list[list] = []

    def fake_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        for needle, result in routes:
            if needle in cmd:
                return result
        return (0, "", "")

    monkeypatch.setattr(mod, "adb", fake_adb)
    return calls


def _rm_calls(calls: list[list]) -> list[list]:
    return [c for c in calls if len(c) > 1 and str(c[1]).startswith("rm -f")]


def _record_push(monkeypatch, mod) -> list[str]:
    pushed: list[str] = []
    monkeypatch.setattr(mod, "push_prefs_xml", lambda content: pushed.append(content))
    return pushed


# ---------------------------------------------------------------------------
# ② set_prefs(reset_count=false)：kind 区分，读不到不以 0 整写
# ---------------------------------------------------------------------------


def test_set_prefs_transient_raises_without_push(lib, monkeypatch):
    """B2 主案：续跑计数读不到（transient）⇒ raise，健康计数不被 0 覆盖。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError) as ei:
        lib.set_prefs(dict(_RESET_CFG))

    assert pushed == [], "transient 下仍整写了完整 prefs（current_count 被覆盖为 0）"
    assert _rm_calls(calls) == []
    assert "重试" in str(ei.value)


def test_set_prefs_denied_raises_without_push(lib, monkeypatch):
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (1, f"cat: {_PREFS_PATH}: Permission denied", "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError):
        lib.set_prefs(dict(_RESET_CFG))

    assert pushed == []
    assert _rm_calls(calls) == []


def test_set_prefs_ok_parses_current_count(lib, monkeypatch):
    """正控制：可读时解析续跑计数，整写保留 42。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    assert lib.set_prefs(dict(_RESET_CFG)) == 42
    assert 'name="current_count" value="42"' in pushed[0]


def test_set_prefs_absent_writes_fresh_map(lib, monkeypatch):
    """正控制：absent 是合法首跑——取 0 并整写 fresh 完整 prefs。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _ABSENT_SENTINEL, "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    assert lib.set_prefs(dict(_RESET_CFG)) == 0
    assert 'name="current_count" value="0"' in pushed[0]
    assert 'name="test_times" value="100"' in pushed[0]
    assert _rm_calls(calls) == []


def test_set_prefs_empty_repaired_then_fresh_write(lib, monkeypatch):
    """有状态反例（#3463 规划者裁定）：首次 probe=``empty`` ⇒ repair 发 ``rm`` ⇒
    第二次 probe=``absent`` ⇒ 以 current_count=0 整写完整 prefs。

    ``empty`` 不是「读不到」而是已确定的损坏（rc=0、文件存在、cat 成功、内容空）；
    损坏文件没有可保留的计数，repair 删除后重建为 0 是唯一结果——与
    ``set_stop_flags`` 的「empty → rm → absent → 最小 map」同一条既有语义。
    固定让每次 probe 都返回 empty 的桩抓不到这条转换，必须按调用次数变状态。
    """
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls: list[list] = []
    probe_n = {"n": 0}

    def stateful_adb(*args, timeout=60):
        calls.append([str(a) for a in args])
        cmd = str(args[1]) if len(args) > 1 else ""
        if cmd.startswith(_PROBE_PREFIX):
            probe_n["n"] += 1
            if probe_n["n"] == 1:
                return (0, "", "")                  # empty：存在 + 读成功 + 内容空
            return (0, _ABSENT_SENTINEL, "")        # repair 删除后重探：absent
        return (0, "", "")

    monkeypatch.setattr(lib, "adb", stateful_adb)
    pushed = _record_push(monkeypatch, lib)

    assert lib.set_prefs(dict(_RESET_CFG)) == 0
    rms = _rm_calls(calls)
    assert len(rms) == 1 and _PREFS_PATH in " ".join(rms[0]), "empty 未触发 repair 删除"
    assert probe_n["n"] == 2, f"探测次数与 empty→rm→absent 序列不符：{probe_n['n']}"
    assert len(pushed) == 1, "fresh 重建未整写完整 prefs"
    assert 'name="current_count" value="0"' in pushed[0]
    assert 'name="test_times" value="100"' in pushed[0]


# ---------------------------------------------------------------------------
# ③ start_task / resume_task：prefs 不可读即 raise，不启动服务
# ---------------------------------------------------------------------------


def test_start_task_unreadable_raises_without_service_start(lib, monkeypatch):
    """B3：读不到（非 ok）⇒ raise，且 `am start`/前台服务不执行。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    pushed = _record_push(monkeypatch, lib)

    with pytest.raises(RuntimeError):
        lib.start_task()

    assert pushed == [], "running=true 未写入却继续启动序列"
    assert not any("am start" in str(c[1]) for c in calls), "服务仍被启动"
    assert not any("am broadcast" in str(c[1]) for c in calls)


def test_resume_task_unreadable_raises(monkeypatch):
    """B3（check 专属）：resume 读不到 ⇒ raise，auto_resume=true 不跳写、不启动。"""
    lib_check = _load("pcg1pc2_lib_check_resume", "powercycle_check/_lib.py")
    monkeypatch.setattr(lib_check, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib_check, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    pushed = _record_push(monkeypatch, lib_check)

    with pytest.raises(RuntimeError):
        lib_check.resume_task()

    assert pushed == []
    assert not any("am start" in str(c[1]) for c in calls)


def test_start_task_readable_writes_running_true(lib, monkeypatch):
    """正控制：可读时在原 map 上置 running=true 并走完整启动序列。"""
    monkeypatch.setattr(lib, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib, [
        (_PROBE_PREFIX, (0, _STOPPED_XML, "")),
    ])
    pushed = _record_push(monkeypatch, lib)

    lib.start_task()

    assert len(pushed) == 1 and 'name="running" value="true"' in pushed[0]
    assert any("am start" in str(c[1]) for c in calls)


# ---------------------------------------------------------------------------
# ④ _verify_stop_flags（finish）：root 优先回读 + kind 区分
# ---------------------------------------------------------------------------


def test_verify_passes_when_root_write_succeeded_despite_runas_denial(monkeypatch):
    """B4 主案：root 写成功（回读 ok 含 running=false）+ run-as 被拒 ⇒ 不得假失败。"""
    lib_finish = _load("pcg1pc2_lib_finish_verify", "powercycle_finish/_lib.py")
    monkeypatch.setattr(lib_finish, "is_root", lambda: True)
    calls = _install_fake_adb(monkeypatch, lib_finish, [
        (_PROBE_PREFIX, (0, _STOPPED_XML, "")),
        # run-as 若被（错误地）用作回读证据，返回恒拒形态
        ("run-as", (1, "run-as: Permission denied", "")),
    ])
    rewrites: list[int] = []
    monkeypatch.setattr(lib_finish, "set_stop_flags", lambda: rewrites.append(1))

    lib_finish._verify_stop_flags()  # 不 raise 即通过

    assert rewrites == [], "root 回读已 ok 仍触发了重写"
    assert _rm_calls(calls) == []


def test_verify_unreadable_retries_then_raises(monkeypatch):
    """④ 后半：真读不到（transient）⇒ 按既有语义重试，仍不 ok 才 raise。"""
    lib_finish = _load("pcg1pc2_lib_finish_verify2", "powercycle_finish/_lib.py")
    monkeypatch.setattr(lib_finish, "is_root", lambda: True)
    _install_fake_adb(monkeypatch, lib_finish, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    _record_push(monkeypatch, lib_finish)

    with pytest.raises(RuntimeError):
        lib_finish._verify_stop_flags()


def test_verify_catches_genuine_write_failure(monkeypatch):
    """正控制：回读 ok 但 running 仍 true（写真失败）⇒ 重写后仍不 ok ⇒ raise。"""
    lib_finish = _load("pcg1pc2_lib_finish_verify3", "powercycle_finish/_lib.py")
    monkeypatch.setattr(lib_finish, "is_root", lambda: True)
    _install_fake_adb(monkeypatch, lib_finish, [
        (_PROBE_PREFIX, (0, _HEALTHY_XML, "")),
    ])
    pushed = _record_push(monkeypatch, lib_finish)

    with pytest.raises(RuntimeError) as ei:
        lib_finish._verify_stop_flags()

    assert "running 未置 false" in str(ei.value)
    assert len(pushed) >= 1, "写失败路径未尝试重写"


# ---------------------------------------------------------------------------
# §9.1 裁定一：check 收取窗口顶层反例（步骤语义不变，raise 由窗口承接）
# ---------------------------------------------------------------------------


def test_check_run_collect_window_absorbs_transient_and_compensates(monkeypatch, tmp_path):
    """真实链 `_run → pause_task → set_stop_flags`：transient raise 被窗口既有
    重试语义承接——prefs 未被覆盖、collect_error 记录、补偿 resume 已尝试、
    `success` 仍为 true（#813：瞬态读失败不得放大为 Plan FAILED）。"""
    entry, lib_check = _load_entry_with_lib("pcg1pc2_check_entry", "powercycle_check")

    monkeypatch.setattr(entry, "device_serial", lambda: "S1-G1PC2")
    monkeypatch.setattr(entry, "_state_file", lambda: tmp_path / "state.json")
    monkeypatch.setattr(entry, "_in_collect_window", lambda cfg, now=None: True)
    monkeypatch.setattr(entry, "device_online", lambda: True)
    monkeypatch.setattr(entry, "service_alive", lambda: False)
    monkeypatch.setattr(entry, "progress_stamp", lambda payload: None)

    def _no_collect(project):
        raise AssertionError("收取不应被执行：pause 已失败")

    monkeypatch.setattr(entry, "collect_powercycle_result", _no_collect)
    resume_attempts: list[int] = []
    monkeypatch.setattr(entry, "resume_task", lambda: resume_attempts.append(1))

    # 打桩打在 entry 实际绑定的同一份 _lib 模块字典上——真实 pause_task/set_stop_flags 生效
    monkeypatch.setattr(lib_check, "is_root", lambda: True)
    lib_calls = _install_fake_adb(monkeypatch, lib_check, [
        (_PROBE_PREFIX, (-1, "", "timeout")),
    ])
    pushed = _record_push(monkeypatch, lib_check)

    r = entry._run({"collect_window_start": "00:00", "project": "smoke"})

    assert r["success"] is True, "瞬态读失败被放大为步骤失败（违反 §9.1 裁定一）"
    assert pushed == [], "transient 下 prefs 被覆盖"
    assert "kind=transient" in r["progress"]["collect_error"], (
        f"collect_error 未承接真实 raise：{r['progress'].get('collect_error')!r}"
    )
    assert resume_attempts == [1], "未尝试补偿 resume"
    assert not any("am start" in str(c[1]) for c in lib_calls), "暂停失败后服务仍被启动"
    assert _rm_calls(lib_calls) == []
    state = json.loads((tmp_path / "state.json").read_text())
    assert state.get("paused_by_collect") is False, "补偿成功后意图标记应清除"
