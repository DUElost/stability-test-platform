"""archive_status 服务单元测试。"""

from backend.models.job import JobArtifact
from backend.services.archive_status import build_host_archive_status


def test_build_host_archive_status_counts_bundles(db_session, sample_job_instance, sample_host):
    db_session.add(JobArtifact(
        job_id=sample_job_instance.id,
        storage_uri=f"/mnt/nfs/archives/2026-06-15/{sample_job_instance.id}.tar.gz",
        artifact_type="run_log_bundle",
        size_bytes=512,
        checksum="abc",
    ))
    db_session.commit()

    payload = build_host_archive_status(db_session, sample_host.id)

    assert payload["host_id"] == sample_host.id
    assert payload["archived_total"] == 1
    assert payload["last_archive_at"] is not None
    assert payload["agent_metrics"] is None


def test_build_host_archive_status_unknown_host(db_session):
    try:
        build_host_archive_status(db_session, "no-such-host")
        assert False, "expected LookupError"
    except LookupError:
        pass
