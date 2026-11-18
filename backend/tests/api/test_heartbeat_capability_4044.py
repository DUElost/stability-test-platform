"""#4044 阶段二：Agent 心跳能力上报的控制面接收与保存。

判据来源 #3800 §2.1 与 #4044「接口与不变量」：能力属于该 host 的**当前 Agent 实例与
当前心跳**，接收侧必须每拍权威覆盖（缺失即撤回），并留下可与 Socket.IO 连接核对的
实例绑定。本文件只覆盖「上报 → 接收 → 落列」；准入结论见
``backend/tests/services/test_agent_capability_evidence_4044.py``，实际发送见
``backend/tests/services/test_shadow_scan_capability_guard_4044.py``。
"""

from __future__ import annotations


import pytest

from backend.agent.contracts.capabilities import SCAN_ROUND_V1
from backend.services.agent_capabilities import (
    REASON_HOST_OFFLINE,
    REASON_CAPABILITY_MISSING,
    REASON_CAPABILITY_NEVER_REPORTED,
    evaluate_host_capability,
)


def _beat(client, host_id, *, instance="agent-new", **extra):
    payload = {"host_id": host_id, "status": "ONLINE", "agent_instance_id": instance, **extra}
    response = client.post("/api/v1/heartbeat", json=payload)
    assert response.status_code == 200, response.text
    return response


def _verdict(db_session, host):
    """刷新后按「当前心跳」判定——now 取 host 自己的心跳时间，避免依赖真实时钟。"""
    db_session.refresh(host)
    return host, evaluate_host_capability(host, now=host.last_heartbeat)


def test_new_agent_heartbeat_persists_capability_evidence(client, sample_host, db_session):
    """能力经接收、保存后可读回：准入判据的事实源是这三列，不是推断。"""
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])

    host, verdict = _verdict(db_session, sample_host)
    assert host.agent_capabilities == [SCAN_ROUND_V1]
    assert host.agent_capabilities_instance_id == "agent-new"
    assert host.agent_capabilities_reported_at == host.last_heartbeat
    assert verdict.verified
    assert verdict.instance_id == "agent-new"
    assert verdict.capabilities == (SCAN_ROUND_V1,)


def test_legacy_agent_payload_without_key_is_accepted_and_withdraws(
    client, sample_host, db_session,
):
    """兼容矩阵「旧 Agent」行：不带 capabilities 键——载荷照收（不 422），但证明立即失效。

    收口前的反例：预筛看 artifact digest，旧 Agent 回退可以沿用上一实例留下的摘要。
    """
    _beat(client, sample_host.id, instance="agent-new", capabilities=[SCAN_ROUND_V1])
    assert _verdict(db_session, sample_host)[1].verified

    _beat(client, sample_host.id, instance="agent-old")

    host, verdict = _verdict(db_session, sample_host)
    assert host.agent_capabilities == []
    assert host.agent_capabilities_instance_id == "agent-old"
    assert not verdict.verified
    assert verdict.reason == REASON_CAPABILITY_MISSING


def test_rollback_then_upgrade_sequence_withdraws_and_restores(
    client, sample_host, db_session,
):
    """fleet 回退到旧构建立刻失去准入；重新升级后由当前心跳恢复，无需人工清列。"""
    _beat(client, sample_host.id, instance="agent-v2", capabilities=[SCAN_ROUND_V1])
    _beat(client, sample_host.id, instance="agent-v1")
    assert not _verdict(db_session, sample_host)[1].verified

    _beat(client, sample_host.id, instance="agent-v3", capabilities=[SCAN_ROUND_V1])
    host, verdict = _verdict(db_session, sample_host)
    assert verdict.verified
    assert host.agent_capabilities_instance_id == "agent-v3"


def test_explicit_empty_capability_list_differs_from_never_reported(
    client, sample_host, db_session,
):
    """``[]`` 是「本构建无能力」的权威陈述；NULL 才是「从未上报」——原因码必须可区分。"""
    _beat(client, sample_host.id, capabilities=[])
    host, verdict = _verdict(db_session, sample_host)
    assert host.agent_capabilities == []
    assert verdict.reason == REASON_CAPABILITY_MISSING

    # NULL 只可能来自迁移前的存量行：直接落库模拟升级前的 host 记录。
    sample_host.agent_capabilities = None
    sample_host.agent_capabilities_reported_at = None
    db_session.commit()
    host, verdict = _verdict(db_session, sample_host)
    assert host.agent_capabilities is None
    assert verdict.reason == REASON_CAPABILITY_NEVER_REPORTED


def test_non_list_capability_payload_is_rejected_without_changing_evidence(
    client, sample_host, db_session,
):
    """能力必须是字符串数组：畸形载荷按 422 拒绝，不得半写证据。"""
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])
    db_session.refresh(sample_host)
    reported_at = sample_host.agent_capabilities_reported_at

    response = client.post("/api/v1/heartbeat", json={
        "host_id": sample_host.id, "status": "ONLINE", "capabilities": SCAN_ROUND_V1,
    })
    assert response.status_code == 422

    host, _ = _verdict(db_session, sample_host)
    assert host.agent_capabilities == [SCAN_ROUND_V1]
    assert host.agent_capabilities_reported_at == reported_at


def test_heartbeat_still_proves_capability_without_artifact_digest(
    client, sample_host, db_session,
):
    """digest 收口：摘要缺失（未部署该协议/文件损坏）不再影响能力证明——
    当前心跳的能力字段单独即可解释候选结论（#4044 验收 1 后半句）。"""
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1], agent_artifact_digest="")

    host, verdict = _verdict(db_session, sample_host)
    assert not host.agent_artifact_digest
    assert verdict.verified


def test_liveness_is_the_existing_online_fact_not_a_second_clock(
    client, sample_host, db_session,
):
    """存活口径沿用现有机制：由 session watchdog 维护的 ``Host.status``。

    能力判据不另算一套墙钟——否则调度器的时钟就成了机队的存活裁判，
    与 #3875 已复核的行为分叉。这里只追加「哪一拍作的证」。
    """
    _beat(client, sample_host.id, capabilities=[SCAN_ROUND_V1])
    db_session.refresh(sample_host)
    assert evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified

    sample_host.status = "OFFLINE"
    db_session.commit()
    verdict = evaluate_host_capability(sample_host, now=sample_host.last_heartbeat)
    assert not verdict.verified
    assert verdict.reason == REASON_HOST_OFFLINE
