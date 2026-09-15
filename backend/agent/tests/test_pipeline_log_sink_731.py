"""#731：step 运行日志从「逐行 open/write/close」改为「每流一个持久句柄」。

分工：
- `test_agent_misc_805.py` 继续守 `_append_log_line` 的**一次性**语义（#805-3：换行
  归一化 / 追加 / OSError 吞掉）——本改动保留了该函数，那些用例不应被改动；
- 本文件守新的 `_StepLogSink` 及其在 `_pump_process` 里的接线，重点是把 issue 的
  **性能声明变成可回归的断言**（每行一次 open 即回归），并覆盖 issue 期望第 3 条
  「正常完成 / abort(timeout) 下缓冲日志都要正确落盘」。
"""

import builtins
import subprocess
import sys
import textwrap

import pytest

from backend.agent.pipeline_engine import (
    _StepLogSink,
    _popen_isolation_kwargs,
    _pump_process,
)


def test_close_sinks_for_abandoned_closes_only_live_readers(tmp_path):
    """#2061：被放弃（仍存活）的 reader 持有的 sink 必须收口。

    正常路径由 reader 自己的 `finally` 收口；被放弃的线程永远不会跑到那里，
    缓冲里的尾部日志（该步骤排障的唯一副本）与 fd 都会留到进程退出。
    """
    from backend.agent.pipeline_engine import _close_sinks_for_abandoned

    live_sink = _StepLogSink(str(tmp_path / "stdout.log"))
    dead_sink = _StepLogSink(str(tmp_path / "stderr.log"))
    live_sink.write("tail-line\n")
    dead_sink.write("already-flushed\n")
    dead_sink.close()  # 正常 reader 已在 finally 收口

    class _Thread:
        def __init__(self, alive: bool) -> None:
            self._alive = alive
            self.name = "step-stdout" if alive else "step-stderr"

        def is_alive(self) -> bool:
            return self._alive

    _close_sinks_for_abandoned([_Thread(True), _Thread(False)], (live_sink, dead_sink))

    assert (tmp_path / "stdout.log").read_text(encoding="utf-8") == "tail-line\n"
    assert live_sink._closed is True
    # 幂等 + None 安全（无 log_paths 时调用方传 None）
    _close_sinks_for_abandoned([_Thread(True)], (live_sink,))
    _close_sinks_for_abandoned([_Thread(True)], None)


def _spawn(body: str) -> subprocess.Popen:
    """必须带 `_popen_isolation_kwargs()`（与 `_run_script_action` 一致）。

    不带的话子进程会继承 pytest 自己的进程组，而超时收尾走 `killpg`
    ——超时用例会把测试进程自己带掉（实测 exit 143）。同 `test_step_stall_detection.py`。
    """
    return subprocess.Popen(
        [sys.executable, "-u", "-c", textwrap.dedent(body)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_popen_isolation_kwargs(),
    )


class _OpenCounter:
    """只统计**目标路径**上的 open 次数，其余调用原样转发（不干扰 pytest 自身 IO）。

    说明：`pathlib.Path.read_text()` 走 `io.open` 而非 `builtins.open`，不会被计入。
    """

    def __init__(self, paths):
        self.paths = {str(p) for p in paths}
        self.counts = {p: 0 for p in self.paths}
        self._real = builtins.open

    def __call__(self, *args, **kwargs):
        if args and str(args[0]) in self.paths:
            self.counts[str(args[0])] += 1
        return self._real(*args, **kwargs)


@pytest.fixture
def count_opens(monkeypatch):
    def _install(*paths) -> _OpenCounter:
        counter = _OpenCounter(paths)
        monkeypatch.setattr(builtins, "open", counter)
        return counter

    return _install


class TestStepLogSink:
    def test_reuses_one_handle_for_many_lines(self, tmp_path, count_opens):
        path = tmp_path / "s.out.log"
        counter = count_opens(path)

        sink = _StepLogSink(str(path))
        for i in range(50):
            sink.write(f"line-{i}")
        sink.close()

        assert counter.counts[str(path)] == 1, "每行一次 open 就是 #731 要修的开销"
        assert path.read_text(encoding="utf-8").splitlines() == [
            f"line-{i}" for i in range(50)
        ]

    def test_close_flushes_tail_without_newline(self, tmp_path):
        path = tmp_path / "s.out.log"
        sink = _StepLogSink(str(path))
        sink.write("no-newline-tail")
        sink.close()

        assert path.read_text(encoding="utf-8") == "no-newline-tail\n"

    def test_write_after_close_is_noop(self, tmp_path):
        path = tmp_path / "s.out.log"
        sink = _StepLogSink(str(path))
        sink.write("a\n")
        sink.close()
        sink.write("b\n")  # reader 已收尾后又来一行：不得复活句柄

        assert path.read_text(encoding="utf-8") == "a\n"

    def test_newline_normalization_matches_805_contract(self, tmp_path):
        path = tmp_path / "s.out.log"
        sink = _StepLogSink(str(path))
        sink.write("with\n")
        sink.write("without")
        sink.close()

        assert path.read_text(encoding="utf-8") == "with\nwithout\n"

    def test_write_failure_swallowed_and_not_retried(self, tmp_path, count_opens):
        missing = tmp_path / "missing" / "s.out.log"
        counter = count_opens(missing)

        sink = _StepLogSink(str(missing))
        for _ in range(5):
            sink.write("x\n")  # 目录不存在：OSError 被吞，不抛出
        sink.close()

        assert counter.counts[str(missing)] == 1, "失败后不得每行重试 open"


class TestPumpWiresPersistentSink:
    def test_normal_completion_logs_all_lines_with_one_open(self, tmp_path, count_opens):
        out = tmp_path / "step.out.log"
        err = tmp_path / "step.err.log"
        counter = count_opens(out, err)

        proc = _spawn(
            """
            import sys as _s
            for i in range(20):
                print(f"out-{i}")
            print("err-0", file=_s.stderr)
            """
        )
        outcome = _pump_process(
            proc, wall_clock=30, stall_seconds=None, log_paths=(str(out), str(err))
        )

        assert outcome.reason is None
        assert out.read_text(encoding="utf-8").splitlines() == [
            f"out-{i}" for i in range(20)
        ]
        assert err.read_text(encoding="utf-8").strip() == "err-0"
        # 每流一个句柄（不是每行一个）
        assert counter.counts[str(out)] == 1
        assert counter.counts[str(err)] == 1

    def test_timeout_still_flushes_lines_written_before_kill(self, tmp_path):
        out = tmp_path / "step.out.log"
        err = tmp_path / "step.err.log"

        proc = _spawn(
            """
            print("before-sleep")
            import time
            time.sleep(60)
            """
        )
        outcome = _pump_process(
            proc, wall_clock=1.5, stall_seconds=None, log_paths=(str(out), str(err))
        )

        assert outcome.reason == "wall_clock"
        # 超时收尾（reader finally → sink.close）必须把已写内容 flush 到盘
        assert "before-sleep" in out.read_text(encoding="utf-8")

    def test_no_log_paths_still_pumps(self):
        proc = _spawn('print("ok")')
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)

        assert outcome.reason is None
        assert outcome.stdout.strip() == "ok"
