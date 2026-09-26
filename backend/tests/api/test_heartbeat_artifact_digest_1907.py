"""ADR-0040 D2（#1907）：心跳上报 agent_artifact_digest 落 Host 显式列。"""

from __future__ import annotations

DIGEST = "sha256:" + "b" * 64
RESOURCES_DIGEST = "sha256:" + "b" * 64


class TestHeartbeatArtifactDigest:
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
