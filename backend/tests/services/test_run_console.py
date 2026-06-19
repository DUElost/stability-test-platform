"""RunConsole 单元测试（ADR-0025 §9 RunConsole）。

控制面命令执行 + 实时控制台。用真 `python -c` 子进程驱动，注入 emit 捕获
SocketIO 推送；放在 agent/tests 仅为复用其轻量(无 PG)conftest，加快迭代。

覆盖：流式+成功完成 / 失败退出码 / run_key 串行 / 取消 / read_log replay /
      emit 事件(console_log + console_status)。
"""

from __future__ import annotations

import sys
import time

import pytest

from backend.services.run_console import RunConsole, RunKeyBusyError


@pytest.fixture(autouse=True)
def reset_singleton():
    RunConsole._reset_for_tests()
    yield
    RunConsole._reset_for_tests()


@pytest.fixture
def emit_capture():
    events: list = []

    def _emit(event, data, room):
        events.append((event, data, room))

    return events, _emit


def _configure(tmp_path, emit):
    return RunConsole.instance().configure(
        log_root=str(tmp_path / "console"),
        encoding="utf-8",
        cancel_grace_seconds=2.0,
        emit=emit,
    )


def _wait_terminal(run_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = RunConsole.instance().status(run_id)
        if st and st["status"] in ("SUCCESS", "FAILED", "CANCELED"):
            return st
        time.sleep(0.05)
    return RunConsole.instance().status(run_id) or {}


def _py(code: str) -> list:
    return [sys.executable, "-c", code]


def test_streams_and_completes_success(tmp_path, emit_capture):
    events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="k1",
        cmd=_py("print('line A'); print('line B'); print('line C')"),
        label="echo3",
    )
    st = _wait_terminal(run_id)

    assert st["status"] == "SUCCESS"
    assert st["exit_code"] == 0
    assert st["seq"] == 3
    # 落盘 replay
    log = rc.read_log(run_id)
    assert log["lines"] == ["line A", "line B", "line C"]
    assert log["seq"] == 3
    # emit：至少一条 console_log + 一条 console_status(终态)
    log_events = [e for e in events if e[0] == "console_log"]
    status_events = [e for e in events if e[0] == "console_status"]
    assert log_events, "应推送 console_log"
    assert status_events and status_events[-1][1]["status"] == "SUCCESS"
    # room 一致
    assert all(e[2] == f"console:{run_id}" for e in events)
    # 推送的行汇总应含全部输出
    pushed = [ln for e in log_events for ln in e[1]["lines"]]
    assert pushed == ["line A", "line B", "line C"]


def test_failed_exit_code(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(run_key="k2", cmd=_py("import sys; print('boom'); sys.exit(3)"))
    st = _wait_terminal(run_id)
    assert st["status"] == "FAILED"
    assert st["exit_code"] == 3


def test_run_key_busy_serial(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    # 长跑 run 占住 key
    run_id = rc.start(
        run_key="same",
        cmd=_py("import time\nfor i in range(100):\n  print(i)\n  time.sleep(0.1)"),
    )
    try:
        with pytest.raises(RunKeyBusyError):
            rc.start(run_key="same", cmd=_py("print('x')"))
    finally:
        rc.cancel(run_id)
        _wait_terminal(run_id)


def test_cancel_running(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="k3",
        cmd=_py("import time\nfor i in range(100):\n  print(i)\n  time.sleep(0.1)"),
    )
    # 等它确实跑起来产出几行
    time.sleep(0.3)
    assert rc.cancel(run_id) is True
    st = _wait_terminal(run_id)
    assert st["status"] == "CANCELED"
    # 取消后 key 释放，可再起
    rid2 = rc.start(run_key="k3", cmd=_py("print('ok')"))
    assert _wait_terminal(rid2)["status"] == "SUCCESS"


def test_read_log_from_seq(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(run_key="k4", cmd=_py("print('a'); print('b'); print('c'); print('d')"))
    _wait_terminal(run_id)
    # from_seq=3(含)→ 返回第 3、4 行
    log = rc.read_log(run_id, from_seq=3)
    assert log["lines"] == ["c", "d"]
    assert log["from_seq"] == 3
    assert log["seq"] == 4


def test_not_configured_raises(tmp_path):
    # 未 configure 直接 start 应报错
    rc = RunConsole.instance()
    with pytest.raises(Exception):
        rc.start(run_key="x", cmd=_py("print('x')"))


def test_shutdown_cancels_inflight(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="k-shutdown",
        cmd=_py("import time\nfor i in range(100):\n  print(i)\n  time.sleep(0.1)"),
    )
    time.sleep(0.3)  # 确实跑起来
    rc.shutdown()
    st = _wait_terminal(run_id)
    assert st["status"] == "CANCELED"
    # shutdown 后 key 释放,可再起
    rid2 = rc.start(run_key="k-shutdown", cmd=_py("print('ok')"))
    assert _wait_terminal(rid2)["status"] == "SUCCESS"


def test_shutdown_no_inflight_is_noop(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    # 无 inflight,shutdown 应安全 no-op
    rc.shutdown()
    # 仍可正常起 run
    rid = rc.start(run_key="k-empty", cmd=_py("print('ok')"))
    assert _wait_terminal(rid)["status"] == "SUCCESS"


def test_shutdown_idempotent(tmp_path, emit_capture):
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="k-idem",
        cmd=_py("import time\nfor i in range(100):\n  print(i)\n  time.sleep(0.1)"),
    )
    time.sleep(0.3)
    rc.shutdown()
    rc.shutdown()  # 二次 shutdown 应安全
    st = _wait_terminal(run_id)
    assert st["status"] == "CANCELED"
