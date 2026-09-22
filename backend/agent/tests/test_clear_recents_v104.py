"""clear_recents v1.0.4（#3104 / #3107）。

#3104：v1.0.3 的判据正确（读失败不得判成功），但 abort 无条件发生在重试循环**内部**
⇒ `max_attempts` 对读失败不可达——一次瞬时 `uiautomator dump` 抖动（正是设备重启窗里
的形态）就让整步变红。v1.0.4 把它改成消耗一次尝试，最后一次仍失败才转红。

#3107：`dump_path` 来自计划参数并被插进设备端 shell（`rm -f` / `cat`），此前无校验：
`"/"` ⇒ `rm -f /`，含元字符的值可扩张成任意 root 命令。v1.0.4 只接受
`/data/local/tmp/<普通文件名>`。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "clear_recents"

_EMPTY_HIERARCHY = '<hierarchy rotation="0"></hierarchy>'


def _load_version(version: str, tag: str):
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


_OK_DUMP = _Proc(0)
_OK_CAT = _Proc(0, stdout=_EMPTY_HIERARCHY)
_FAIL_DUMP = _Proc(1, stderr="ERROR: could not get idle state.")


def _install_stubs(monkeypatch, mod, *, dump_procs, cat_procs):
    """按调用序派发：第 N 次 dump / cat 取列表第 N 项（越界则复用最后一项）。"""
    calls: list[str] = []
    state = {"dump": 0, "cat": 0}

    def fake_shell(command: str, timeout: int = 30) -> str:
        calls.append(command)
        return ""

    def fake_shell_quiet(command: str, timeout: int = 30) -> _Proc:
        calls.append(command)
        if "uiautomator dump" in command:
            idx = min(state["dump"], len(dump_procs) - 1)
            state["dump"] += 1
            return dump_procs[idx]
        if command.startswith("cat "):
            idx = min(state["cat"], len(cat_procs) - 1)
            state["cat"] += 1
            return cat_procs[idx]
        return _Proc(0)

    monkeypatch.setattr(mod, "adb_shell", fake_shell)
    monkeypatch.setattr(mod, "adb_shell_quiet", fake_shell_quiet)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    return calls


def _run_main(monkeypatch, mod, *, dump_procs, cat_procs, step_params=None):
    calls = _install_stubs(monkeypatch, mod, dump_procs=dump_procs, cat_procs=cat_procs)
    if step_params is not None:
        monkeypatch.setenv("STP_STEP_PARAMS", json.dumps(step_params))
    mod.main()
    return calls


def _payload(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "main() 未产出 stdout JSON"
    return json.loads(out[-1])


# ---------------------------------------------------------------------------
# #3104：读失败消耗一次尝试
# ---------------------------------------------------------------------------


def test_transient_read_failure_is_retried_and_can_succeed(monkeypatch, capsys):
    """首尝试 dump 抖一下、第二尝试成功 ⇒ 应判绿（v1.0.3 会直接红）。"""
    mod = _load_version("v1.0.4", "cr_v104")
    _run_main(
        monkeypatch, mod,
        dump_procs=[_FAIL_DUMP, _OK_DUMP], cat_procs=[_OK_CAT],
        step_params={"max_attempts": 2},
    )
    payload = _payload(capsys)
    assert payload["success"] is True, "瞬时读失败被当成终局失败（#3104 未修）"
    m = payload["metrics"]
    assert m["attempts"] == 2, "第二次尝试没有真的跑"
    assert len(m["read_failures"]) == 1, "读失败证据必须保留"
    assert m["ui_read_failed"] is False


def test_read_failure_after_all_attempts_is_red(monkeypatch, capsys):
    """每次都读失败（真故障）⇒ 转红，且证据齐全。"""
    mod = _load_version("v1.0.4", "cr_v104")
    _run_main(
        monkeypatch, mod,
        dump_procs=[_FAIL_DUMP], cat_procs=[_OK_CAT],
        step_params={"max_attempts": 3},
    )
    payload = _payload(capsys)
    assert payload["success"] is False
    m = payload["metrics"]
    assert m["ui_read_failed"] is True
    assert m["attempts"] == 3, f"应跑满 3 次尝试，实际 {m['attempts']}"
    assert len(m["read_failures"]) == 3
    assert m["tasks_after"] is None, "读失败不得编造 tasks_after"
    assert "UI 层级读取失败" in payload["error_message"]


def test_v103_was_not_retrying_this_is_the_regression_pin(monkeypatch, capsys):
    """对照锚点：同一输入在 v1.0.3 下只跑一次就红（旧版本不可变，只读断言）。"""
    mod = _load_version("v1.0.3", "cr_v103")
    _run_main(
        monkeypatch, mod,
        dump_procs=[_FAIL_DUMP, _OK_DUMP], cat_procs=[_OK_CAT],
        step_params={"max_attempts": 2},
    )
    payload = _payload(capsys)
    assert payload["success"] is False
    assert payload["metrics"]["attempts"] == 1, "v1.0.3 的 max_attempts 对读失败不可达"


# ---------------------------------------------------------------------------
# #3107：dump_path 校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["/", "//", "/data", "/data/local/tmp/../etc/x", "/sdcard/x.xml",
     "/data/local/tmp/x; rm -rf /", "/data/local/tmp/a b.xml", 42],
)
def test_invalid_dump_path_rejected(bad):
    mod = _load_version("v1.0.4", "cr_v104")
    with pytest.raises(ValueError):
        mod.validated_dump_path(bad)


@pytest.mark.parametrize(
    "good",
    [None, "", "/data/local/tmp/stp_clear_recents.xml", "/data/local/tmp/a-b_c.1.xml"],
)
def test_valid_dump_path_accepted(good):
    mod = _load_version("v1.0.4", "cr_v104")
    assert mod.validated_dump_path(good).startswith("/data/local/tmp/")


def test_invalid_dump_path_fails_step_without_touching_device(monkeypatch, capsys):
    """非法 dump_path ⇒ 直接红，且**不得**下发任何含该值的命令。"""
    mod = _load_version("v1.0.4", "cr_v104")
    calls = _run_main(
        monkeypatch, mod,
        dump_procs=[_OK_DUMP], cat_procs=[_OK_CAT],
        step_params={"dump_path": "/"},
    )
    payload = _payload(capsys)
    assert payload["success"] is False
    assert "dump_path" in payload["error_message"]
    assert not any("rm -f /" == c.strip() for c in calls), calls
    assert not any(c.strip() == "cat /" for c in calls), calls
    assert not any("uiautomator dump /" in c for c in calls), calls
