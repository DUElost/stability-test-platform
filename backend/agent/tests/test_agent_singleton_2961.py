"""#2961：同机单实例守卫 —— 第二个 Agent 进程必须立刻失败退出。

判据来自 2026-07-27 事故：systemd 实例与手工 ``venv/bin/python -m agent.main``
并存，两者同 HOST_ID、各持不同 instance_id，心跳互相覆盖
``host.last_agent_instance_id``，coordinator fencing 对任一实例都判 stale，
全平台心跳被拒 3 天。守卫的价值就在于**第二个进程不可能静默跑起来**。

#3092 追加：锁文件读写打开失败时回退只读 flock（守卫生效但不写 pid），
``O_NOFOLLOW`` 拒绝跟随符号链接；连只读都失败才降级，且降级必须可告警
（``single_instance_guard_degraded()`` → 心跳 health.reasons → 指标 → 告警）。
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

#: 权限类用例只在非 root 下有效——root 无视文件模式位，测不出 EACCES 回退。
_NOT_ROOT = getattr(os, "geteuid", lambda: 1)() != 0
requires_non_root = pytest.mark.skipif(
    not _NOT_ROOT, reason="root 无视 0o444/0o000 模式位，权限回退路径不可达"
)


@pytest.fixture(autouse=True)
def _reset_guard_state(monkeypatch):
    """降级标志是进程级单例——逐用例复位，避免用例间互相污染判定。"""
    monkeypatch.setattr(startup_guards, "_degraded_reason", None)


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
    """锁文件建不出来时降级放行，不阻塞整机 Agent 启动。

    #3092：降级不再是「一行 warning」——必须置位可上报的降级事实。
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    assert startup_guards.enforce_single_instance(str(blocker / "agent.lock")) is None
    assert startup_guards.single_instance_guard_degraded() is True


@requires_non_root
def test_readonly_lockfile_still_guards(tmp_path):
    """#3092：读写打开失败（EACCES）→ 只读回退，守卫照常拦住第二个实例。

    形态来自现网：锁文件被别的属主创建为 0644，android 用户可读不可写。
    只读 fd 的 flock 同样生效（Linux 实测），只是不再写 pid。
    """
    lock = tmp_path / "agent.lock"
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o444)

    first = startup_guards.enforce_single_instance(str(lock))
    assert first is not None, "只读回退后守卫必须仍生效"
    assert startup_guards.single_instance_guard_degraded() is False

    with pytest.raises(SystemExit) as excinfo:
        startup_guards.enforce_single_instance(str(lock))
    assert excinfo.value.code == 1

    os.close(first)
    assert lock.read_text(encoding="utf-8") == "", "只读路径不得写/截断锁文件"


@requires_non_root
def test_unreadable_lockfile_degrades_and_reports(tmp_path):
    """#3092：连只读都打不开（如 0600 root）→ 降级启动 + 降级事实置位。"""
    lock = tmp_path / "agent.lock"
    lock.write_text("", encoding="utf-8")
    lock.chmod(0o000)

    assert startup_guards.enforce_single_instance(str(lock)) is None
    assert startup_guards.single_instance_guard_degraded() is True


def test_symlink_lockfile_is_not_followed(tmp_path):
    """#3092：O_NOFOLLOW —— 符号链接锁文件不跟随、不写链接目标。"""
    target = tmp_path / "target"
    target.write_text("do not touch\n", encoding="utf-8")
    link = tmp_path / "agent.lock"
    link.symlink_to(target)

    assert startup_guards.enforce_single_instance(str(link)) is None
    assert startup_guards.single_instance_guard_degraded() is True
    assert target.read_text(encoding="utf-8") == "do not touch\n", (
        "跟随了符号链接并截断/写入了链接目标"
    )


def test_heartbeat_reports_guard_degradation():
    """#3092 接线守卫：心跳必须把降级事实传给 compute_capacity。

    失败形态是「判定写了但上不了报」——降级标记置位而心跳不读，等于没有。
    与 test_kernel_usb_faults 的接线守卫同思路（AST 读源码，agent 测试自足）。
    """
    import ast

    import backend.agent.heartbeat_thread as hb_mod

    source = Path(hb_mod.__file__).read_text(encoding="utf-8")
    calls = [node for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call)]
    capacity_kwargs = [
        kw.arg for call in calls
        for kw in call.keywords
        if isinstance(call.func, ast.Name) and call.func.id == "compute_capacity"
    ]
    assert "single_instance_degraded" in capacity_kwargs, (
        "心跳未把 single_instance_degraded 传给 compute_capacity——降级事实不可见"
    )


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
