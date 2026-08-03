"""步骤停滞判据（#115 阶段 1）。

用**真实子进程**测，不 mock：这里要守的是管道并发读、超时杀树、线程回收这
几件事，全都只在真跑起来时才会错。

最要紧的一条是「零行为变更」：全部 17 个脚本都用 ``capture_output=True``
吞掉子进程输出，实测 14 个从头到尾零输出。所以「任意输出 = 活」这条判据在
当前脚本集上等价于「全体判死」—— 停滞钟必须缺省关闭，逐个 PlanStep 打开。
"""

import subprocess
import sys
import textwrap
import time

import pytest

from backend.agent.pipeline_engine import (
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


class TestReaderThreadsDoNotLeak:
    def test_threads_are_joined_after_kill(self):
        import threading

        before = {t.name for t in threading.enumerate()}
        proc = _spawn("import time; time.sleep(60)")
        _pump_process(proc, wall_clock=1, stall_seconds=None)
        time.sleep(_POLL_INTERVAL_SECONDS)
        leaked = {t.name for t in threading.enumerate()} - before
        assert not {n for n in leaked if n.startswith("step-")}, leaked


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
