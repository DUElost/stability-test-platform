"""ADR-0040 D2（#1907）：心跳上报 agent_artifact_digest 落 Host 显式列。"""

from __future__ import annotations

from datetime import timedelta

from backend.services.agent_capabilities import (
    REASON_CAPABILITY_NEVER_REPORTED,
    REASON_CAPABILITY_NOT_CURRENT,
    REASON_CAPABILITY_MISSING,
    REASON_HOST_OFFLINE,
    evaluate_host_capability,
)

CAPABLE = ["scan_round_v1"]

DIGEST = "sha256:" + "b" * 64
RESOURCES_DIGEST = "sha256:" + "b" * 64


class TestHeartbeatArtifactDigest:
    def test_shadow_guard_rejects_retained_digest_after_capability_withdrawal(
        self, client, sample_host, db_session,
    ):
        # #3875 过渡判据由 #4044 收口：摘要保留（digest 列不覆盖）不再能替当前心跳
        # 作证——旧 Agent 心跳撤回能力后，即使 host 上仍留着匹配摘要也必须判未证实。
        cases = [
            (DIGEST, "new-agent", CAPABLE, True),
            (DIGEST, "old-agent", [], False),
            (DIGEST, "updated-agent", CAPABLE, True),
        ]
        for digest, instance, capabilities, expected in cases:
            response = client.post("/api/v1/heartbeat", json={
                "host_id": sample_host.id, "status": "ONLINE", "agent_instance_id": instance,
                "agent_artifact_digest": digest,
                "capabilities": capabilities,
            })
            assert response.status_code == 200
            db_session.refresh(sample_host)
            assert sample_host.agent_artifact_digest == DIGEST
            assert evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified is expected
            # 每拍都写能力列：能力是正向断言，缺失即撤回，不存在「保留上一实例的证明」。
            assert sample_host.agent_capabilities_reported_at == sample_host.last_heartbeat
            assert sample_host.agent_capabilities_instance_id == instance
            if not expected:
                assert evaluate_host_capability(
                    sample_host, now=sample_host.last_heartbeat).reason == REASON_CAPABILITY_MISSING

    def test_shadow_guard_requires_online_and_current_heartbeat(self, sample_host):
        sample_host.agent_capabilities = CAPABLE
        sample_host.agent_capabilities_instance_id = "new-agent"
        sample_host.agent_capabilities_reported_at = sample_host.last_heartbeat
        assert evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified
        sample_host.status = "OFFLINE"
        assert not evaluate_host_capability(
            sample_host, now=sample_host.last_heartbeat).verified
        assert evaluate_host_capability(
            sample_host, now=sample_host.last_heartbeat).reason == REASON_HOST_OFFLINE
        sample_host.status = "ONLINE"
        # 摘要列落后于当前心跳也不再影响准入（digest 判据已由 #4044 收口）。
        sample_host.agent_artifact_digest = DIGEST
        sample_host.agent_artifact_digest_reported_at = (
            sample_host.last_heartbeat - timedelta(seconds=120)
        )
        # 摘要列已不参与准入：只有能力上报时间落后于当前心跳才失效。
        assert evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified
        sample_host.agent_capabilities_reported_at -= timedelta(seconds=1)
        assert not evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified
        assert evaluate_host_capability(
            sample_host, now=sample_host.last_heartbeat).reason == REASON_CAPABILITY_NOT_CURRENT
        sample_host.agent_capabilities_reported_at = None
        assert not evaluate_host_capability(sample_host, now=sample_host.last_heartbeat).verified
        assert evaluate_host_capability(
            sample_host, now=sample_host.last_heartbeat).reason == REASON_CAPABILITY_NEVER_REPORTED

    def test_heartbeat_persists_artifact_digest(self, client, sample_host, db_session):
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "agent_artifact_digest": DIGEST,
            },
        )
        assert response.status_code == 200
        db_session.refresh(sample_host)
        assert sample_host.agent_artifact_digest == DIGEST

    def test_heartbeat_empty_digest_does_not_overwrite(self, client, sample_host, db_session):
        sample_host.agent_artifact_digest = DIGEST
        db_session.commit()

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "agent_artifact_digest": "",
            },
        )
        assert response.status_code == 200
        db_session.refresh(sample_host)
        # digest 缺失（未部署新协议/文件损坏）不应抹掉已知状态
        assert sample_host.agent_artifact_digest == DIGEST


# ── ADR-0040 D8 R4：resources 身份停写（旧 Agent 照收、不落列） ──────────────────


class TestHeartbeatResourcesDigestRetired:
    def test_legacy_resources_digest_is_accepted_but_not_written(self, client, sample_host, db_session):
        """旧 Agent 仍会带 agent_resources_digest：载荷照收（不 422），但控制面不再写列——
        停写前的存量值原样保留（列在版本窗口后随迁移删除）。

        反例（R4 前）：心跳会把 RESOURCES_DIGEST 写进 host.agent_resources_digest。
        """
        legacy = "sha256:" + "c" * 64
        sample_host.agent_resources_digest = legacy
        db_session.commit()

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "agent_resources_digest": RESOURCES_DIGEST,
            },
        )
        assert response.status_code == 200
        db_session.refresh(sample_host)
        assert sample_host.agent_resources_digest == legacy
