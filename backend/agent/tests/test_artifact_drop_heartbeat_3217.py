"""#3217：crash artifact 的提交数与三路丢失随心跳上报（Agent 侧）。

控制面侧（Gauge / 告警输入 / 四数对拍）见 ``backend/tests/api/test_heartbeat_artifact_drop_3217.py``。
"""

from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock, patch

import pytest

from backend.agent.artifact_uploader import ArtifactUploader
from backend.agent.heartbeat_bindings import RecoveryActionsSlot, build_heartbeat_thread

_KEYS = (
    "artifact_submits_total",
    "artifact_dropped_submit_total",
    "artifact_dropped_promote_total",
    "artifact_dropped_post_total",
)


def test_heartbeat_counts_mirror_each_loss_path():
    """三条丢失路径各自一个键，不合并——成因与处置不同（容量 / 存储 / 网络后端）。"""
    u = ArtifactUploader()
    u.stats.submits_total = 10
    u.stats.submits_dropped = 3
    u.stats.promote_failed = 2
    u.stats.posts_failed = 1
    u.stats.posts_ok = 4  # 成功与幂等命中不进上报：分母用提交数
    assert u.heartbeat_counts() == {
        "artifact_submits_total": 10,
        "artifact_dropped_submit_total": 3,
        "artifact_dropped_promote_total": 2,
        "artifact_dropped_post_total": 1,
    }


def test_heartbeat_counts_payload_budget():
    """新增键的心跳载荷增量（紧凑 JSON）：实测最坏 150B（提交 7 位数、三路各 6 位数），典型 132B。

    单独计本 issue 的预算，不并进 #2902 / #2957 的断言（同一先例的理由：各 issue 只约束各自的增量）。
    比那两条宽，是因为沿用了既有 ``*_total`` 长键名（与 ``log_signal_dead_letter_total`` 等一致）；
    相对每拍心跳里 25 台设备的明细约占 1%。
    """
    u = ArtifactUploader()
    u.stats.submits_total = 9_999_999
    u.stats.submits_dropped = u.stats.promote_failed = u.stats.posts_failed = 999_999
    assert len(json.dumps(u.heartbeat_counts(), separators=(",", ":"))) < 160


@pytest.mark.parametrize(
    ("capacity", "injected", "expected_dropped"),
    [
        pytest.param(4, 3, 0, id="below-capacity"),
        pytest.param(4, 4, 0, id="exactly-full"),
        pytest.param(4, 7, 3, id="over-capacity"),
    ],
)
def test_flood_tiers_count_exactly(capacity, injected, expected_dropped):
    """洪峰三档（临界以下 / 恰好触顶 / 超限）：丢弃数必须**精确**等于溢出数。

    worker 被替换成只等待、不取条目，队列恰好装满 ``capacity`` 条——避免既有队满用例
    「首条是否已被 worker 拎走」的时序不确定（那条只能断言 ``>= 1``）。
    """
    u = ArtifactUploader()
    blocker = threading.Event()
    u._worker_loop = lambda: blocker.wait(5.0)  # 不消费：队列深度 = 已入队数
    u.configure(
        api_url="http://x", host_id="host-3217", agent_instance_id="agent-3217",
        session=MagicMock(), queue_maxsize=capacity,
    )
    u.start()
    try:
        for i in range(injected):
            u.submit(job_id=1, artifact_type="aee_crash", storage_uri=f"/crash/{i}",
                     fencing_token="1:1", device_serial="S-3217")
        counts = u.heartbeat_counts()
        assert counts["artifact_submits_total"] == injected
        assert counts["artifact_dropped_submit_total"] == expected_dropped
    finally:
        blocker.set()
        u.stop(drain=False, timeout=1.0)


def test_heartbeat_outbox_counts_carry_artifact_keys():
    """接线：心跳 ``get_outbox_counts`` 必须带上这四个键（只改 uploader 而漏接线 = 本机仍不可见）。"""
    stub = MagicMock()
    stub.heartbeat_counts.return_value = dict.fromkeys(_KEYS, 7)
    local_db = MagicMock()
    local_db.count_pending_terminals.return_value = 0
    local_db.count_pending_log_signals.return_value = 0
    local_db.count_log_signal_dead_letters.return_value = 0
    local_db.count_terminal_dead_letters.return_value = 0
    with (
        patch("backend.agent.heartbeat_bindings.HeartbeatThread") as ht_cls,
        patch("backend.agent.heartbeat_bindings.ArtifactUploader.instance", return_value=stub),
    ):
        ht_cls.return_value = MagicMock()
        build_heartbeat_thread(
            api_url="http://x", host_id="h", adb_path="adb", mount_points=[], host_info={},
            poll_interval=5.0, sio_client=MagicMock(), script_registry=MagicMock(version=1),
            local_db=local_db, mq_producer=MagicMock(), agent_instance_id="inst", boot_id="boot",
            agent_version="1.0.0", agent_code_revision="rev", active_jobs_lock=threading.Lock(),
            active_job_ids=set(), active_device_ids=set(), recovery_actions_slot=RecoveryActionsSlot(),
        )
        counts = ht_cls.call_args.kwargs["get_outbox_counts"]()
    for key in _KEYS:
        assert counts[key] == 7, key
    assert "terminal_outbox_pending" in counts, "既有键不得被挤掉"
