"""步骤停滞判据（#115 阶段 1）。

用**真实子进程**测，不 mock：这里要守的是管道并发读、超时杀树、线程回收这
几件事，全都只在真跑起来时才会错。

最要紧的一条是「零行为变更」：全部 17 个脚本都用 ``capture_output=True``
吞掉子进程输出，实测 14 个从头到尾零输出。所以「任意输出 = 活」这条判据在
当前脚本集上等价于「全体判死」—— 停滞钟必须缺省关闭，逐个 PlanStep 打开。
"""

import logging
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from backend.agent.pipeline_engine import (
    _MAX_CAPTURED_CHARS,
    _POLL_INTERVAL_SECONDS,
    _popen_isolation_kwargs,
    _pump_process,
    _resolve_step_stall_seconds,
)


def _spawn(body: str) -> subprocess.Popen:
    """必须带 _popen_isolation_kwargs()，与 _run_script_action 一致。

    不带的话子进程会继承 pytest 自己的进程组，而 _terminate_process_tree 走的是
    ``killpg`` —— 于是超时那几条用例会把测试进程自己 SIGTERM 掉（实测 exit 143）。
    """
    return subprocess.Popen(
        [sys.executable, "-u", "-c", textwrap.dedent(body)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **_popen_isolation_kwargs(),
    )


class TestResolveStallSeconds:
    def test_disabled_by_default(self, monkeypatch):
        """缺省关闭是承重的，不是保守。

        开了就等于把 push(预算 600s)、fill(预算 300s) 和将来的刷机步骤
        一起在缺省窗口内杀掉，183 台同时。
        """
        monkeypatch.delenv("STP_STEP_STALL_SECONDS", raising=False)
        assert _resolve_step_stall_seconds({}) is None

    def test_plan_step_value_wins(self, monkeypatch):
        monkeypatch.setenv("STP_STEP_STALL_SECONDS", "30")
        assert _resolve_step_stall_seconds({"stall_seconds": 600}) == 600.0

    def test_env_provides_a_fleet_default(self, monkeypatch):
        monkeypatch.setenv("STP_STEP_STALL_SECONDS", "120")
        assert _resolve_step_stall_seconds({}) == 120.0

    def test_env_global_enable_warns_once(self, monkeypatch, caplog):
        """env 是全机开关，绕过逐个 PlanStep 的灰度闸门 —— 首次生效必须告警，
        但不能每个步骤都刷一遍日志。"""
        import backend.agent.pipeline_engine as pe

        monkeypatch.setattr(pe, "_env_stall_global_warned", False)
        monkeypatch.setenv("STP_STEP_STALL_SECONDS", "120")
        with caplog.at_level(logging.WARNING, logger="backend.agent.pipeline_engine"):
            assert pe._resolve_step_stall_seconds({}) == 120.0
            assert pe._resolve_step_stall_seconds({}) == 120.0
        warns = [
            r.message
            for r in caplog.records
            if r.message.startswith("STP_STEP_STALL_SECONDS=")
        ]
        assert len(warns) == 1

    @pytest.mark.parametrize("value", [0, -1, "nonsense"])
    def test_zero_negative_and_garbage_all_disable(self, monkeypatch, value):
        """出错时倒向"不杀"。凭空给一个没人 opt-in 的步骤发明停滞预算会误杀健康作业。"""
        monkeypatch.delenv("STP_STEP_STALL_SECONDS", raising=False)
        assert _resolve_step_stall_seconds({"stall_seconds": value}) is None


class TestPumpBehaviourUnchangedWhenStallDisabled:
    def test_silent_long_script_is_not_killed(self):
        """**核心回归**：静默脚本在未配 stall_seconds 时必须活到自然结束。

        这正是当前 17 个脚本的形态。
        """
        proc = _spawn("""
            import time
            time.sleep(3)
            print('{"success": true}')
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.reason is None
        assert proc.returncode == 0
        assert outcome.stdout.strip() == '{"success": true}'

    def test_fast_script_still_works(self):
        proc = _spawn('print("done")')
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.reason is None
        assert outcome.stdout.strip() == "done"

    def test_wall_clock_still_kills(self):
        proc = _spawn("import time; time.sleep(60)")
        outcome = _pump_process(proc, wall_clock=2, stall_seconds=None)
        assert outcome.reason == "wall_clock"
        assert proc.poll() is not None


class TestStallDetection:
    def test_silent_script_is_killed_once_enabled(self):
        proc = _spawn("import time; time.sleep(60)")
        started = time.monotonic()
        outcome = _pump_process(proc, wall_clock=None, stall_seconds=2)
        assert outcome.reason == "stall"
        # 靠停滞钟死的，不是耗到总时长 —— wall_clock=None 本来就没有总时长
        assert time.monotonic() - started < 10

    def test_steady_progress_stamps_keep_it_alive_past_the_stall_window(self):
        """每 0.5s 一戳 PROGRESS、共 3s，停滞钟 2s —— 不该死。"""
        proc = _spawn("""
            import sys, time
            for i in range(6):
                sys.stderr.write('PROGRESS {"seq": %d}\n' % i); sys.stderr.flush()
                time.sleep(0.5)
            print('{"success": true}')
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=2)
        assert outcome.reason is None, outcome.stderr

    def test_output_then_silence_is_killed(self):
        """先有输出再卡死 —— 停滞钟从最后一行之后开始算。"""
        proc = _spawn("""
            import time
            print("started", flush=True)
            time.sleep(60)
        """)
        outcome = _pump_process(proc, wall_clock=None, stall_seconds=2)
        assert outcome.reason == "stall"
        assert "started" in outcome.stdout

    def test_plain_output_does_not_count_as_progress(self):
        """**活锁场景**：持续打印普通日志、但从不打 PROGRESS 戳 —— 必须判死。

        这正是停滞钟存在的意义：fastboot 无限重试、adb install 卡 90% 反复
        重连打印日志，都会持续输出。若普通输出也算活，停滞钟就形同虚设。
        """
        proc = _spawn("""
            import sys, time
            for _ in range(6):
                sys.stderr.write("retrying...\\n"); sys.stderr.flush()
                time.sleep(0.5)
            sys.stderr.write("eventually done\\n")
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=2)
        assert outcome.reason == "stall"
        assert "retrying" in outcome.stderr


class TestProgressStamps:
    def test_progress_lines_reset_the_stall_clock(self):
        proc = _spawn("""
            import sys, time
            for i in range(6):
                sys.stderr.write('PROGRESS {"seq": %d}\\n' % i); sys.stderr.flush()
                time.sleep(0.5)
            print('{"success": true}')
        """)
        seen = []
        outcome = _pump_process(
            proc, wall_clock=30, stall_seconds=2, on_progress=lambda: seen.append(1),
        )
        assert outcome.reason is None
        assert len(seen) == 6

    def test_progress_lines_never_enter_the_buffers(self):
        """12h 步骤每 5s 一戳 = 8640 行，会把真正的报错挤出 64KiB 截断窗口。"""
        proc = _spawn("""
            import sys
            for i in range(50):
                sys.stderr.write('PROGRESS {"seq": %d}\\n' % i)
            sys.stderr.write("real error here\\n")
            print('{"success": true}')
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert "PROGRESS" not in outcome.stderr
        assert "real error here" in outcome.stderr

    def test_stdout_json_contract_survives_line_reading(self):
        """stdout 整份要过 json.loads —— 逐行读之后必须原样重组。"""
        import json

        proc = _spawn("""
            import json
            print(json.dumps({"success": True, "metrics": {"steps": {"a": 1}}}))
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert json.loads(outcome.stdout.strip())["metrics"]["steps"]["a"] == 1


class TestProgressLeadingWhitespace:
    def test_progress_with_leading_whitespace_resets_stall_clock(self):
        """#147: PROGRESS 前带缩进/日志前缀时仍须刷新停滞钟并丢弃该行。"""
        proc = _spawn("""
            import sys, time
            for i in range(4):
                sys.stderr.write('  PROGRESS {"seq": %d}\\n' % i)
                sys.stderr.flush()
                time.sleep(0.4)
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=1.5)
        assert outcome.reason is None
        assert "PROGRESS" not in outcome.stderr


class TestCaptureLimit:
    def test_output_over_8mib_is_truncated_without_deadlock(self, caplog):
        """#147: 超过 8MiB 的单流捕获上限必须截断、继续读、进程正常退出。"""
        proc = _spawn("""
            import sys
            line = "x" * 1023 + "\\n"
            for _ in range(9000):
                sys.stdout.write(line)
        """)
        with caplog.at_level(
            logging.WARNING, logger="backend.agent.pipeline_engine"
        ):
            outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert proc.returncode == 0
        assert len(outcome.stdout) <= _MAX_CAPTURED_CHARS
        assert any(
            "step_output_capture_limit_reached" in r.message
            for r in caplog.records
        )


class TestReaderThreadsDoNotLeak:
    def test_threads_are_joined_after_kill(self):
        before = {t.name for t in threading.enumerate()}
        proc = _spawn("import time; time.sleep(60)")
        _pump_process(proc, wall_clock=1, stall_seconds=None)
        time.sleep(_POLL_INTERVAL_SECONDS)
        leaked = {t.name for t in threading.enumerate()} - before
        assert not {n for n in leaked if n.startswith("step-")}, leaked

    def test_reader_interrupted_when_grandchild_holds_pipe_open(self):
        """真实泄漏场景：孙进程 setsid 脱离进程组并持有管道写端（adb server 形态）。

        修复前：reader 永久阻塞在 readline()，join 超时后留下 daemon 线程，
        长驻 agent 每个卡死步骤 +1。修复后：POSIX 轮询 reader 必须被 stop
        打断，步骤返回时不留 step-* 线程，已收到的行照常返回。
        """
        if sys.platform == "win32":
            pytest.skip("Windows 管道不可 select，保留阻塞 reader 兜底")
        before = {t.name for t in threading.enumerate()}
        proc = _spawn("""
            import os, sys, time
            pid = os.fork()
            if pid == 0:
                os.setsid()       # 脱离进程组：killpg 扫不到
                time.sleep(5)     # 期间一直持有 stdout/stderr 写端
                os._exit(0)
            print("done", flush=True)
            os._exit(0)
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.stdout.strip() == "done"
        time.sleep(0.5)
        leaked = {t.name for t in threading.enumerate()} - before
        assert not {n for n in leaked if n.startswith("step-")}, leaked


class TestPollingReaderSemantics:
    """POSIX 轮询 reader 必须保持 readline() 的既有语义（Windows 跳过）。"""

    @pytest.fixture(autouse=True)
    def _skip_on_windows(self):
        if sys.platform == "win32":
            pytest.skip("Windows 走阻塞 readline 兜底，不经轮询 reader")

    def test_partial_final_line_is_preserved(self):
        proc = _spawn('import sys; sys.stdout.write("no-newline-at-end")')
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.stdout == "no-newline-at-end"

    def test_crlf_normalised_to_lf(self):
        """TextIOWrapper(newline=None) 的既有行为：\\r\\n / 孤立 \\r → \\n。"""
        proc = _spawn('import sys; sys.stdout.write("a\\r\\nb\\r\\nc")')
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.stdout == "a\nb\nc"

    def test_crlf_progress_stamp_still_resets_stall_clock(self):
        """\\r\\n 结尾的 PROGRESS 行必须同样被识别（dd status=progress 类输出）。"""
        proc = _spawn("""
            import sys, time
            for i in range(4):
                sys.stderr.write('PROGRESS {"seq": %d}\\r\\n' % i)
                time.sleep(0.4)
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=1.5)
        assert outcome.reason is None

    def test_invalid_utf8_does_not_kill_the_reader(self):
        """旧 TextIOWrapper(strict) 遇到坏字节会炸掉整个 reader（丢输出/可能堵管道）；
        轮询 reader 用 replace 解码，坏字节之后的内容照常送达。"""
        proc = _spawn("""
            import sys
            sys.stdout.buffer.write(b"\\xff\\xfe")
            sys.stdout.buffer.write(b"ok\\n")
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert outcome.stdout.endswith("ok\n")


class TestProgressOnlyOnStderr:
    def test_stdout_progress_looking_lines_are_not_swallowed(self):
        """stdout 整份要过 json.loads，是既有结果契约 —— 哪怕内容以 PROGRESS 开头。

        stdout reader 不识别 PROGRESS，全部原样进缓冲。
        """
        proc = _spawn("""
            import json
            print("PROGRESS " + json.dumps({"seq": 1}))
        """)
        outcome = _pump_process(proc, wall_clock=30, stall_seconds=None)
        assert "PROGRESS" in outcome.stdout


def test_pump_process_writes_full_log_files(tmp_path):
    """2026-08-31：运行日志唯一副本——stdout/stderr 全量落盘（含 PROGRESS）。"""
    import subprocess
    import sys

    out_log = tmp_path / "step.out.log"
    err_log = tmp_path / "step.err.log"
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import sys; print('hello-out'); "
         "print('PROGRESS {\"seq\":1}', file=sys.stderr); "
         "print('hello-err', file=sys.stderr)"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    outcome = _pump_process(
        proc, wall_clock=30, stall_seconds=None,
        log_paths=(str(out_log), str(err_log)),
    )
    assert outcome.reason is None
    assert "hello-out" in out_log.read_text(encoding="utf-8")
    err_text = err_log.read_text(encoding="utf-8")
    assert "PROGRESS" in err_text       # PROGRESS 也落盘（缓冲里被 drop 不影响文件）
    assert "hello-err" in err_text
