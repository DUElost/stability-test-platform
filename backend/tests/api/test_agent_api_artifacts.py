"""Agent API /jobs/{job_id}/artifacts 契约测试（ADR-0018 5B2）。

覆盖：
  1. happy path  ：合法 payload → 201 created + artifact_id 返回
  2. 幂等        ：同 (job_id, storage_uri) 二次 POST 返回同 id + created=False
  3. 白名单      ：artifact_type 不在 {aee_crash, vendor_aee_crash, bugreport} → 400
  4. storage_uri ：空串 → 400
  5. size_bytes  ：负数 → 400
  6. job 不存在  ：404
  7. 溯源字段    ：source_category / source_path_on_device 写入成功
  8. 幂等不覆盖  ：二次 POST 带不同 checksum 也不更新原行（ON CONFLICT DO NOTHING 语义）

与 test_agent_api_watcher.py 相同，本文件仅在 PostgreSQL 下跑（pg_insert ON CONFLICT）。
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("DATABASE_URL", "").startswith("sqlite"),
    reason="API 契约测试需要 PostgreSQL（pg_insert + 跨 engine 种子数据）；"
           "SQLite quick-test 模式下自动跳过。",
)

from fastapi import HTTPException

from backend.api.routes.agent_api import ArtifactIn, ingest_artifact
from backend.core.database import AsyncSessionLocal, SessionLocal, async_engine
from backend.models.enums import JobStatus
from backend.models.job import JobArtifact
from backend.tests.api.test_agent_api_watcher import (
    _cleanup_seed,
    _seed_job_with_policy,
)


# ----------------------------------------------------------------------
# helper
# ----------------------------------------------------------------------

def _cleanup_with_artifacts(seed: dict) -> None:
    """扩展清理：先删本用例产生的 artifact 再走标准 cleanup。"""
    db = SessionLocal()
    try:
        job_id = seed.get("job_id")
        if job_id:
            db.query(JobArtifact).filter(JobArtifact.job_id == job_id).delete()
            db.commit()
    finally:
        db.close()
    _cleanup_seed(seed)


# ----------------------------------------------------------------------
# 1. happy path
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_happy_path_creates_new_row():
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            result = await ingest_artifact(
                job_id=seed["job_id"],
                payload=ArtifactIn(
                    storage_uri="/mnt/nfs/stability/jobs/1/AEE/1700000000_db.0.0",
                    artifact_type="aee_crash",
                    size_bytes=1024,
                    checksum="a" * 64,
                    source_category="AEE",
                    source_path_on_device="/data/aee_exp/db.0.0",
                ),
                db=async_db,
                _=None,
            )
        assert result.error is None
        assert result.data.created is True
        assert result.data.artifact_id > 0

        db = SessionLocal()
        try:
            art = db.get(JobArtifact, result.data.artifact_id)
            assert art is not None
            assert art.job_id == seed["job_id"]
            assert art.storage_uri.endswith("db.0.0")
            assert art.artifact_type == "aee_crash"
            assert art.size_bytes == 1024
            assert art.checksum == "a" * 64
            assert art.source_category == "AEE"
            assert art.source_path_on_device == "/data/aee_exp/db.0.0"
        finally:
            db.close()
    finally:
        _cleanup_with_artifacts(seed)


# ----------------------------------------------------------------------
# 2. 幂等：同 (job_id, storage_uri) 二次 POST → created=False + 同 id
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_is_idempotent_on_job_storage_uri():
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    storage_uri = "/mnt/nfs/stability/jobs/1/VENDOR_AEE/1700000000_db.x"
    payload = ArtifactIn(
        storage_uri=storage_uri,
        artifact_type="vendor_aee_crash",
        size_bytes=2048,
        checksum="b" * 64,
        source_category="VENDOR_AEE",
    )
    try:
        # 首次
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r1 = await ingest_artifact(
                job_id=seed["job_id"], payload=payload, db=async_db, _=None,
            )
        assert r1.data.created is True
        first_id = r1.data.artifact_id

        # 再次：同 storage_uri 但不同 checksum / size —— 端点必须忽略变化
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            r2 = await ingest_artifact(
                job_id=seed["job_id"],
                payload=ArtifactIn(
                    storage_uri=storage_uri,
                    artifact_type="vendor_aee_crash",
                    size_bytes=9999,
                    checksum="c" * 64,
                    source_category="VENDOR_AEE",
                ),
                db=async_db,
                _=None,
            )
        assert r2.data.created is False
        assert r2.data.artifact_id == first_id, "幂等命中必须返回同一 id"

        # DB 仍只有 1 行，且保留首次的字段值
        db = SessionLocal()
        try:
            rows = db.query(JobArtifact).filter(
                JobArtifact.job_id == seed["job_id"],
                JobArtifact.storage_uri == storage_uri,
            ).all()
            assert len(rows) == 1
            assert rows[0].size_bytes == 2048
            assert rows[0].checksum == "b" * 64
        finally:
            db.close()
    finally:
        _cleanup_with_artifacts(seed)


# ----------------------------------------------------------------------
# 3. artifact_type 白名单
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_rejects_unlisted_artifact_type():
    """首期只接受 {aee_crash, vendor_aee_crash, bugreport}；其他一律 400。"""
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as excinfo:
                await ingest_artifact(
                    job_id=seed["job_id"],
                    payload=ArtifactIn(
                        storage_uri="/mnt/nfs/x",
                        artifact_type="anr_trace",  # 不在白名单
                    ),
                    db=async_db,
                    _=None,
                )
        assert excinfo.value.status_code == 400
        assert "artifact_type" in str(excinfo.value.detail).lower()

        # 未入库
        db = SessionLocal()
        try:
            assert db.query(JobArtifact).filter(
                JobArtifact.job_id == seed["job_id"]
            ).count() == 0
        finally:
            db.close()
    finally:
        _cleanup_with_artifacts(seed)


# ----------------------------------------------------------------------
# 4. storage_uri 空串
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_rejects_empty_storage_uri():
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as excinfo:
                await ingest_artifact(
                    job_id=seed["job_id"],
                    payload=ArtifactIn(
                        storage_uri="",
                        artifact_type="aee_crash",
                    ),
                    db=async_db,
                    _=None,
                )
        assert excinfo.value.status_code == 400
        assert "storage_uri" in str(excinfo.value.detail).lower()
    finally:
        _cleanup_with_artifacts(seed)


# ----------------------------------------------------------------------
# 5. size_bytes 负数
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_rejects_negative_size_bytes():
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    try:
        await async_engine.dispose()
        async with AsyncSessionLocal() as async_db:
            with pytest.raises(HTTPException) as excinfo:
                await ingest_artifact(
                    job_id=seed["job_id"],
                    payload=ArtifactIn(
                        storage_uri="/mnt/nfs/jobs/1/aee/file",
                        artifact_type="aee_crash",
                        size_bytes=-1,
                    ),
                    db=async_db,
                    _=None,
                )
        assert excinfo.value.status_code == 400
        assert "size_bytes" in str(excinfo.value.detail).lower()
    finally:
        _cleanup_with_artifacts(seed)


# ----------------------------------------------------------------------
# 6. job 不存在
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_returns_404_when_job_missing():
    await async_engine.dispose()
    async with AsyncSessionLocal() as async_db:
        with pytest.raises(HTTPException) as excinfo:
            await ingest_artifact(
                job_id=999999999,
                payload=ArtifactIn(
                    storage_uri="/mnt/nfs/nope",
                    artifact_type="aee_crash",
                ),
                db=async_db,
                _=None,
            )
    assert excinfo.value.status_code == 404
    assert "job" in str(excinfo.value.detail).lower()


# ----------------------------------------------------------------------
# 7. 白名单全集合 smoke test：三种类型都能成功入库
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ingest_artifact_accepts_all_whitelisted_types():
    seed = _seed_job_with_policy(job_status=JobStatus.RUNNING.value)
    whitelist = ["aee_crash", "vendor_aee_crash", "bugreport"]
    try:
        for i, at in enumerate(whitelist):
            await async_engine.dispose()
            async with AsyncSessionLocal() as async_db:
                r = await ingest_artifact(
                    job_id=seed["job_id"],
                    payload=ArtifactIn(
                        storage_uri=f"/mnt/nfs/jobs/1/{at}/{i}",
                        artifact_type=at,
                    ),
                    db=async_db,
                    _=None,
                )
            assert r.error is None
            assert r.data.created is True

        db = SessionLocal()
        try:
            rows = db.query(JobArtifact).filter(
                JobArtifact.job_id == seed["job_id"]
            ).all()
            assert sorted(r.artifact_type for r in rows) == sorted(whitelist)
        finally:
            db.close()
    finally:
        _cleanup_with_artifacts(seed)
