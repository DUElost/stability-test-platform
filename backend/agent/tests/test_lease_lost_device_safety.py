# -*- coding: utf-8 -*-
"""#799：lease 丢失后「先停手、占位留到进程退出」的契约测试。

覆盖三层语义：
1. ``handle_lease_lost`` 对活跃 job 派发 abort（runner.cancel/killpg），同时
   把 job 摘出活跃集合（``_is_aborted()`` 真）并唤醒 permit 等待者；
2. 设备占位保留到 worker 真正退出（``JobRunnerState.release`` 带 device_id
   快照的归属感知补偿才清）——避免设备立即回池被另一台 host 领走（跨 host 双驱）；
3. 无活跃 job（无 runner 可杀）时立即清占位，不把设备无谓锁死。
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import backend.agent.main as agent_main
from backend.agent.job_runner import JobRunnerState


class _FakeRunner:
    def __init__(self) -> None:
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1


def _make_state():
    lock = threading.Lock()
    ids: set = set()
    device_ids: set = set()
    tokens: dict = {}
    owners: dict = {}

    def lock_register(
        jid: int, token: str = "", device_id=None, device_serial: str = "",
        local_worker_token: str = "",
    ) -> None:
        with lock:
            ids.add(jid)
            tokens[jid] = local_worker_token or token
            if device_id is not None:
                device_ids.add(device_id)
                owners[device_id] = jid

    def lock_deregister(jid: int, token: str = "", local_worker_token: str = "") -> None:
        with lock:
            ids.discard(jid)
            tokens.pop(jid, None)

    state = JobRunnerState(
        active_jobs_lock=lock,
        active_job_ids=ids,
        active_device_ids=device_ids,
        active_job_tokens=tokens,
        running_worker_tokens={},
        watcher_globally_enabled=False,
        watcher_plan_default=False,
        lock_register=lock_register,
        lock_deregister=lock_deregister,
        device_id_register=lambda did: None,
        device_id_deregister=lambda did: None,
        active_device_owner=owners,
    )
    return state, owners


def _handle(state, job_id: int, device_id, coordinator):
    return agent_main.handle_lease_lost(
        job_id=job_id,
        device_id=device_id,
        job_runner_state=state,
        coordinator=coordinator,
        active_jobs_lock=state.active_jobs_lock,
        active_job_ids=state.active_job_ids,
        active_device_ids=state.active_device_ids,
        active_job_tokens=state.active_job_tokens,
        active_device_owner=state.active_device_owner,
        local_db=None,
    )


def test_lease_lost_kills_runner_and_keeps_device_slot_until_release():
    state, owners = _make_state()
    state.lock_register(26, "63:6", 63, "SERIAL-63")
    runner = _FakeRunner()
    state.attach_runner(26, "63:6", runner)
    coordinator = MagicMock()

    abort_dispatched = _handle(state, 26, 63, coordinator)

    assert abort_dispatched is True
    assert runner.cancel_calls == 1      # 在跑脚本被杀（killpg 入口）
    assert 26 not in state.active_job_ids  # 摘活跃集合 → _is_aborted() 真
    assert 63 in state.active_device_ids   # 设备占位保留
    assert owners[63] == 26
    coordinator.cancel_waiting_job.assert_called_once_with(26)

    # worker 真正退出：release 带 device_id 快照（归属匹配）→ 占位才清
    state.release(26, "63:6", 63)
    assert 63 not in state.active_device_ids
    assert 63 not in owners


def test_lease_lost_without_active_job_releases_slot_immediately():
    """无活跃 job（request_abort=False）时立即清占位，不把设备无谓锁死。"""
    state, owners = _make_state()
    state.active_device_ids.add(63)   # 历史残留占位形状
    owners[63] = 99
    coordinator = MagicMock()

    abort_dispatched = _handle(state, 26, 63, coordinator)

    assert abort_dispatched is False
    assert 63 not in state.active_device_ids   # 本地派发护栏立即解除
    # 归属属于其他 job（99）：按既有归属语义不回清（#1203 防护），仅解除护栏
    assert owners[63] == 99


def test_main_wires_lease_lost_to_shared_helper():
    """接线回归：main._on_lease_lost 必须走共享 helper（含 keep 占位语义）。"""
    text = Path(agent_main.__file__).read_text(encoding="utf-8")

    assert "handle_lease_lost(" in text
    assert "keep_device_slot=abort_dispatched" in text
