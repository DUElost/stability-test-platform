"""archive_status 服务单元测试。"""

from backend.models.job import JobArtifact, JobInstance
from backend.services.archive_status import build_host_archive_status


def test_build_host_archive_status_counts_bundles(db_session, chain_setup):
    cur_run = chain_setup["current_run"]
    job = (
        db_session.query(JobInstance)
        .filter(JobInstance.plan_run_id == cur_run.id)
        .first()
    )
    assert job is not None
    db_session.add(JobArtifact(
        job_id=job.id,
        storage_uri=f"/mnt/nfs/archives/2026-06-15/{job.id}/{job.id}.tar.gz",
        artifact_type="run_log_bundle",
        size_bytes=512,
        checksum="abc",
    ))
    db_session.commit()

    payload = build_host_archive_status(db_session, job.host_id)

    assert payload["host_id"] == job.host_id
    assert payload["archived_total"] == 1
    assert payload["last_archive_at"] is not None
    assert payload["agent_metrics"] is None


def test_build_host_archive_status_unknown_host(db_session):
    try:
        build_host_archive_status(db_session, "no-such-host")
        assert False, "expected LookupError"
    except LookupError:
        pass
