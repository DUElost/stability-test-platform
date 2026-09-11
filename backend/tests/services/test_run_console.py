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

from backend.services.run_console import RunConsole, RunConsoleError, RunKeyBusyError


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


def test_flushes_single_line_while_process_stays_quiet(tmp_path, emit_capture, monkeypatch):
    """#1118: 一行输出后长时间安静，仍应在 flush 间隔内推送（不等到进程结束）。"""
    events, emit = emit_capture
    monkeypatch.setattr(RunConsole, "_FLUSH_MAX_INTERVAL", 0.05)
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="quiet",
        cmd=_py(
            "import sys, time\n"
            "print('early', flush=True)\n"
            "time.sleep(2)\n"
            "print('late', flush=True)\n"
        ),
    )

    deadline = time.time() + 1.0
    while time.time() < deadline:
        pushed = [
            ln
            for e in events
            if e[0] == "console_log"
            for ln in e[1]["lines"]
        ]
        if "early" in pushed:
            st = RunConsole.instance().status(run_id) or {}
            assert st.get("status") == "RUNNING"
            assert "late" not in pushed
            break
        time.sleep(0.02)
    else:
        pytest.fail("early line was not flushed while process remained quiet")

    st = _wait_terminal(run_id)
    assert st["status"] == "SUCCESS"
    pushed = [
        ln
        for e in events
        if e[0] == "console_log"
        for ln in e[1]["lines"]
    ]
    assert pushed == ["early", "late"]


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


# ── #1115：父退出 ≠ 整组退出 —— 忽略 SIGTERM 的后代必须被组级 SIGKILL ────


def _spawn_parent_exits_first_cmd(grandchild_code: str) -> list:
    """父进程起一个忽略 SIGTERM 的子孙后立刻退出；子孙继承 stdout 管道。"""
    import os

    assert hasattr(os, "killpg"), "POSIX-only"
    parent_code = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-c', {grandchild_code!r}])\n"
        "time.sleep(0.5)\n"  # 给子孙装 SIG_IGN 的时间，否则测不出组级收敛
        "print('parent exiting', flush=True)\n"
    )
    return _py(parent_code)


def test_cancel_kills_descendants_ignoring_sigterm(tmp_path, emit_capture):
    """父退出 + 后代忽略 SIGTERM → 必须组级 SIGKILL 收敛（R11-F07）。

    后代握着 stdout 管道写端：它不死，reader 等不到 EOF、_finalize 不跑、
    run_key 不释放 —— 所以「同 key 能立即重起」就是后代已死的可观察证据。
    """
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    grandchild_code = (
        "import signal, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "while True: time.sleep(0.1)\n"
    )
    run_id = rc.start(
        run_key="kg",
        cmd=_spawn_parent_exits_first_cmd(grandchild_code),
    )
    time.sleep(0.3)  # 等 run 起来（parent 还活着，pgid 已留存）
    assert rc.cancel(run_id) is True
    st = _wait_terminal(run_id, timeout=15.0)
    assert st["status"] == "CANCELED"
    # run_key / reader 均已释放：同 key 可立即重起
    rid2 = rc.start(run_key="kg", cmd=_py("print('ok')"))
    assert _wait_terminal(rid2)["status"] == "SUCCESS"


def test_start_captures_pgid_for_late_cancel(tmp_path, emit_capture):
    """#1115：spawn 时留存组身份 —— 父被 reader 回收后 cancel 仍能按组收敛。"""
    import os

    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="kpg",
        cmd=_py("import time\nfor i in range(100):\n  print(i)\n  time.sleep(0.1)"),
    )
    time.sleep(0.3)
    run = rc._runs.get(run_id)
    assert run is not None and run._pgid is not None
    if hasattr(os, "killpg"):
        assert os.killpg(run._pgid, 0) is None  # 组还活着（不抛即活）
    rc.cancel(run_id)
    _wait_terminal(run_id)


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
    with pytest.raises(RunConsoleError):
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


# ── #1124：replay 有界 + 终态运行记录淘汰 ────────────────────────────────


def test_read_log_is_bounded_by_max_lines(tmp_path, emit_capture, monkeypatch):
    """大日志增量 replay：响应行数有上限，seq 仍精确统计到文件末尾。"""
    monkeypatch.setenv("STP_RUN_CONSOLE_REPLAY_MAX_LINES", "10")
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = "con-boundary01"
    log_path = tmp_path / "console" / f"{run_id}.log"
    log_path.write_text("".join(f"line {i}\n" for i in range(1, 2101)), encoding="utf-8")

    from backend.services.run_console import ConsoleRun

    rc._runs[run_id] = ConsoleRun(
        run_id=run_id, run_key="kb", label="big", _log_path=log_path,
    )
    out = rc.read_log(run_id)
    assert len(out["lines"]) == 10
    assert out["lines"][0] == "line 1"
    assert out["seq"] == 2100, "seq 必须是全文件行数，不被上限截断"

    tail = rc.read_log(run_id, from_seq=2096)
    assert tail["from_seq"] == 2096
    assert tail["lines"] == [f"line {i}" for i in range(2096, 2101)]
    assert tail["seq"] == 2100


def test_read_log_truncates_oversized_line(tmp_path, emit_capture):
    """单行超长也不得撑爆响应内存（replay 显示层截断）。"""
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = "con-boundary02"
    log_path = tmp_path / "console" / f"{run_id}.log"
    log_path.write_text("x" * 150_000 + "\n" + "ok\n", encoding="utf-8")

    from backend.services.run_console import ConsoleRun

    rc._runs[run_id] = ConsoleRun(
        run_id=run_id, run_key="kc", label="long", _log_path=log_path,
    )
    out = rc.read_log(run_id)
    assert len(out["lines"][0]) <= 100_000
    assert out["lines"][1] == "ok"
    assert out["seq"] == 2


def test_terminal_runs_evicted_after_retention(tmp_path, emit_capture, monkeypatch):
    """终态超保留期的 run 从 _runs 淘汰；replay 仍可按文件回读（status=UNKNOWN）。"""
    monkeypatch.setenv("STP_RUN_CONSOLE_TERMINAL_RETENTION_SECONDS", "1")
    _events, emit = emit_capture
    rc = _configure(tmp_path, emit)

    run_id = rc.start(run_key="ke", cmd=_py("print('bye')"))
    st = _wait_terminal(run_id)
    assert st["status"] == "SUCCESS"
    assert rc.status(run_id) is not None  # 保留期内仍可查

    # 人为把 ended_at 回拨到保留期之外
    from datetime import datetime, timedelta, timezone as tz

    run = rc._runs[run_id]
    run.ended_at = (datetime.now(tz.utc) - timedelta(seconds=120)).isoformat()

    assert rc.status(run_id) is None, "超保留期的终态 run 应被淘汰"
    # replay 仍走文件回退路径
    out = rc.read_log(run_id)
    assert out["status"] == "UNKNOWN"
    assert out["lines"] == ["bye"]


def test_flush_is_serialized_across_threads(tmp_path):
    """#1275: reader 线程（buf 达阈值）与 0.1s 定时线程并发 flush 必须串行。

    不串行时「摘批 → 分配 seq」与「落盘/推送」两步可交错：先摘到 seq 的批可能
    后落盘，使文件行序 ≠ seq 序（read_log 回溯与实时 from_seq 错位）。用 emit
    回调在第一批推送时阻塞，观察是否有第二个 flush 并发进入 emit。
    """
    import threading

    events: list = []
    first_emit_entered = threading.Event()
    release_first = threading.Event()
    inflight = {"cur": 0, "max": 0}
    guard = threading.Lock()

    def _emit(event, data, room):
        with guard:
            inflight["cur"] += 1
            inflight["max"] = max(inflight["max"], inflight["cur"])
            is_first = event == "console_log" and not first_emit_entered.is_set()
            if is_first:
                first_emit_entered.set()
        try:
            if is_first:
                # 阻塞首个 flush，给第二个 flush 一个并发进入 emit 的机会。
                release_first.wait(timeout=2.0)
            events.append((event, data, room))
        finally:
            with guard:
                inflight["cur"] -= 1

    rc = _configure(tmp_path, _emit)
    # 先 20 行（不足 _FLUSH_MAX_LINES=50，reader 不 flush；由定时线程先摘批并阻塞在
    # emit），随后 200 行让 reader 达到阈值再次调用 flush——不串行时两者并发进入
    # emit（max=2），串行时后者阻塞在 flush 锁（max=1）。
    cmd = (
        "import sys, time\n"
        "for i in range(20): print('A%d' % i)\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.4)\n"
        "for i in range(200): print('L%d' % i)\n"
    )
    run_id = rc.start(run_key="k-flush", cmd=_py(cmd), label="burst")
    assert first_emit_entered.wait(timeout=5.0), "应产生首个 console_log emit"
    # 给 reader 线程足够时间在首个 flush 阻塞期间再次触发 flush。
    time.sleep(0.8)
    release_first.set()

    st = _wait_terminal(run_id)
    assert st["status"] == "SUCCESS"
    log = rc.read_log(run_id)
    assert log["lines"] == (
        ["A%d" % i for i in range(20)] + ["L%d" % i for i in range(200)]
    )
    assert inflight["max"] == 1, "flush 未串行：两个 flush 并发进入 emit（落盘/推送可交错）"


def test_child_env_is_allowlisted_not_inherited(tmp_path, emit_capture, monkeypatch):
    """#1228: 子进程不继承控制面环境（凭据不透传）；白名单键与显式注入保留。"""
    import json

    monkeypatch.setenv("DATABASE_URL", "postgresql://prod-secret")
    monkeypatch.setenv("AGENT_SECRET", "leak-me-not")
    events, emit = emit_capture
    rc = _configure(tmp_path, emit)
    run_id = rc.start(
        run_key="envprobe",
        cmd=_py(
            "import json, os\n"
            "print(json.dumps({k: os.environ.get(k) for k in "
            "['DATABASE_URL', 'AGENT_SECRET', 'PATH', 'STP_PROBE', "
            "'PYTHONUNBUFFERED']}))\n"
        ),
        env={"STP_PROBE": "injected"},
    )
    st = _wait_terminal(run_id)
    assert st["status"] == "SUCCESS"

    payload = json.loads(rc.read_log(run_id)["lines"][0])
    assert payload["DATABASE_URL"] is None, "控制面 DATABASE_URL 不得透传"
    assert payload["AGENT_SECRET"] is None, "凭据不得透传"
    assert payload["PATH"]  # 白名单保留进程启动所需键
    assert payload["STP_PROBE"] == "injected"  # 调用方显式注入生效
    assert payload["PYTHONUNBUFFERED"] == "1"
