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


# ── #1963 P2 切片①：resources 身份落列 ─────────────────────────────────────


class TestHeartbeatResourcesDigest:
    def test_heartbeat_persists_resources_digest(self, client, sample_host, db_session):
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
        assert sample_host.agent_resources_digest == RESOURCES_DIGEST

    def test_heartbeat_empty_resources_digest_no_overwrite(self, client, sample_host, db_session):
        sample_host.agent_resources_digest = RESOURCES_DIGEST
        db_session.commit()
        response = client.post(
            "/api/v1/heartbeat",
            json={"host_id": sample_host.id, "status": "ONLINE"},
        )
        assert response.status_code == 200
        db_session.refresh(sample_host)
        assert sample_host.agent_resources_digest == RESOURCES_DIGEST
