"""#2961：同机单实例守卫 —— 第二个 Agent 进程必须立刻失败退出。

判据来自 2026-07-27 事故：systemd 实例与手工 ``venv/bin/python -m agent.main``
并存，两者同 HOST_ID、各持不同 instance_id，心跳互相覆盖
``host.last_agent_instance_id``，coordinator fencing 对任一实例都判 stale，
全平台心跳被拒 3 天。守卫的价值就在于**第二个进程不可能静默跑起来**。
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from backend.agent import startup_guards

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_second_acquisition_is_refused(tmp_path):
    """同一锁文件的第二次取锁必须 sys.exit(1)。"""
    lock = tmp_path / "agent.lock"

    first = startup_guards.enforce_single_instance(str(lock))
    assert first is not None, "首次取锁不应降级"

    with pytest.raises(SystemExit) as excinfo:
        startup_guards.enforce_single_instance(str(lock))
    assert excinfo.value.code == 1

    os.close(first)
    # 释放后锁不粘滞：下一次启动照常
    again = startup_guards.enforce_single_instance(str(lock))
    assert again is not None
    os.close(again)


def test_cross_process_refusal(tmp_path):
    """真·两个进程：子进程持锁时本进程取锁失败。

    这是 pidfile 式守卫测不到的一层——锁的归属跟着进程走，进程死了内核自动放锁。
    """
    lock = tmp_path / "agent.lock"
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(REPO_ROOT)!r})
        from backend.agent.startup_guards import enforce_single_instance
        fd = enforce_single_instance({str(lock)!r})
        assert fd is not None
        print("locked", flush=True)
        time.sleep(60)
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "locked"

        with pytest.raises(SystemExit) as excinfo:
            startup_guards.enforce_single_instance(str(lock))
        assert excinfo.value.code == 1
        # 锁文件里记的是**持有者**的 pid（第二进程不得改写它）
        assert lock.read_text(encoding="utf-8").strip() == str(proc.pid)
    finally:
        proc.kill()
        proc.wait(timeout=10)

    # 持有者进程被杀后内核自动释放：无需清理锁文件即可再取
    released = startup_guards.enforce_single_instance(str(lock))
    assert released is not None
    os.close(released)


def test_lock_file_records_own_pid(tmp_path):
    """持有者 pid 落盘——冲突日志里 ``holder_pid=`` 才有内容。"""
    lock = tmp_path / "agent.lock"
    fd = startup_guards.enforce_single_instance(str(lock))
    try:
        assert lock.read_text(encoding="utf-8").strip() == str(os.getpid())
    finally:
        os.close(fd)


def test_unavailable_lock_path_is_fail_open(tmp_path):
    """锁文件建不出来时降级放行，不阻塞整机 Agent 启动。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    assert startup_guards.enforce_single_instance(str(blocker / "agent.lock")) is None


def test_entry_takes_lock_before_running_application():
    """入口顺序：先取锁，再构造/运行应用（心跳、注册、上报都在应用内部）。

    守卫站在 ``run_agent_application``（``main`` 唯一调用点）而不是 ``main`` 里，
    是为了不撑破 ``backend/agent/main.py`` 的 34 行封顶（#736 god-files）。
    """
    import backend.agent.agent_application as app_module
    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(app_module).anchored("def run_agent_application()")
    guard.assert_present(
        "enforce_single_instance()",
        why="#2961 单实例守卫必须挂在进程入口",
    )

    source = Path(app_module.__file__).read_text(encoding="utf-8")
    entry = source.index("def run_agent_application()")
    assert source.index("enforce_single_instance()", entry) < source.index(
        "AgentApplication().run()", entry
    ), "守卫必须早于 AgentApplication().run() —— 晚于它就等于没有守卫"
