"""B8 G6 第 5 轮探针：Agent 回执 503 后的重启恢复。

第 5 次复制已成功、VERIFIED 回执在落库前收到 503 时，重启后的 uploader
必须核验同一发布身份的既有目标；一致才重发 VERIFIED/attempts=5，并让请求
真正穿过 FastAPI 回执路由落库（httpx.ASGITransport + 隔离 PG16）。

默认状态库（主 Agent 的 BASE_DIR/agent_state.db）与显式 STP_AGENT_STATE_DB
两组都覆盖；目标缺失/哈希不符/路径含 symlink 的拒绝由 Agent 单元探针覆盖。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.agent import event_uploader as event_uploader_module
from backend.agent import evidence_uploader as uploader_module
from backend.agent.evidence_uploader import EvidenceUploader
from backend.api.routes import agent_evidence as agent_evidence_route
from backend.core import artifact_paths, storage_root
from backend.core.database import SessionLocal
from backend.main import fastapi_app
from backend.models.device_log_event import DeviceLogEvent
from backend.models.host import Host
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun
from backend.services import issue_fact_store as facts

SECRET = "g6-r5-probe-secret"
FOLDER = "MLD-NX3-P535AA-HONOR-NX3-16-1031V78_1031_MonkeyAEEinfo"
EVENT = "2026_1031_120000_123_db.74.ANR"
SERIAL = "AZ6Q123456"


def _create_state_table(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS agent_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed(tmp_path, monkeypatch) -> dict:
    local_root = tmp_path / "local"
    log_root = tmp_path / "log"
    local_root.mkdir()
    log_root.mkdir()
    monkeypatch.setenv("STP_AEE_LOCAL_ROOT", str(local_root))
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(log_root))
    monkeypatch.setenv("AGENT_SECRET", SECRET)
    monkeypatch.setenv("TESTING", "1")
    for module in (artifact_paths, storage_root):
        monkeypatch.setattr(module, "resolve_shared_storage_root", lambda: str(log_root))
        monkeypatch.setattr(
            module, "resolve_control_plane_storage_root", lambda: str(tmp_path / "cp"),
        )
        monkeypatch.setattr(module, "resolve_legacy_log_storage_root", lambda: "")
    (tmp_path / "cp").mkdir()

    host_id = f"g6r5-{uuid4().hex[:8]}"
    now = datetime.now(timezone.utc)
    local_event = local_root / FOLDER / SERIAL / "aee_exp" / EVENT
    local_event.mkdir(parents=True)
    (local_event / "main.dbg").write_bytes(b"dbg")

    db = SessionLocal()
    try:
        db.add(Host(
            id=host_id, hostname=host_id, name=host_id, ip="192.0.2.221",
            ip_address="192.0.2.221", status="ONLINE", last_heartbeat=now,
        ))
        plan = Plan(name=f"g6r5-{uuid4()}", created_by="pytest")
        db.add(plan)
        db.flush()
        run = PlanRun(
            plan_id=plan.id, status="RUNNING", plan_snapshot={},
            run_type="MANUAL", triggered_by="pytest",
        )
        db.add(run)
        db.flush()
        facts.record_partition_mode(
            db, plan_run_id=run.id, platform="mtk",
            mode=facts.PARTITION_MODE_DEDUP_FIRST,
        )
        dle = DeviceLogEvent(
            id=uuid4(), serial=SERIAL, platform="MTK", event_type="JE", state="LOCAL",
            local_path=str(local_event), host_id=host_id, plan_run_id=run.id,
            occurrence_key=uuid4().hex, detected_at=now,
        )
        db.add(dle)
        db.flush()
        signature = facts.record_issue_signature(
            db, signature_raw='{"exp_class": "JE"}', normalized_key=f"key-{uuid4()}",
            platform="mtk", event_type="JE", seen_at=now,
        )
        occurrence = facts.record_issue_occurrence(
            db, signature_id=signature.id, occurrence_key=uuid4().hex, serial=SERIAL,
            observed_at=now, first_seen_source="local",
            first_seen_plan_run_id=run.id,
        )
        facts.link_occurrence_event(
            db, occurrence_id=occurrence.id, dle_id=dle.id, signature_id=signature.id,
        )
        facts.record_representative_selection(
            db, plan_run_id=run.id, platform="mtk", signature_id=signature.id,
            selection_status=facts.SELECTION_SELECTED, dle_id=dle.id,
            evidence_status="COMPLETE",
        )
        rel_path = f"evidence/{run.id}_{FOLDER}/{SERIAL}/{EVENT}"
        publication = facts.record_evidence_publication(
            db, plan_run_id=run.id, platform="mtk", dle_id=dle.id,
            root_plan_run_id=run.id, source_kind=facts.PUBLICATION_SOURCE_LOCAL,
            source_path=str(local_event), collection_folder=FOLDER,
            naming_rule_version="aee-folder-name/v1",
            destination_rel_path=rel_path, executor_host_id=host_id,
        )
        facts.update_evidence_publication_state(
            db, row=publication, state=facts.PUBLICATION_STATE_RETRYING, attempts=4,
        )
        db.commit()
        return {
            "host_id": host_id,
            "publication_id": publication.id,
            "dle_id": str(dle.id),
            "run_id": run.id,
            "local_path": str(local_event),
            "destination_rel_path": rel_path,
            "log_root": log_root,
        }
    finally:
        db.close()


def _task(seed: dict, *, attempts: int = 4) -> dict:
    return {
        "publication_id": seed["publication_id"],
        "dle_id": seed["dle_id"],
        "local_path": seed["local_path"],
        "destination_rel_path": seed["destination_rel_path"],
        "root_plan_run_id": seed["run_id"],
        "serial": SERIAL,
        "event_dir": EVENT,
        "attempts": attempts,
    }


def _configure(uploader, seed) -> EvidenceUploader:
    uploader.configure(
        api_url="http://cp", agent_secret=SECRET, host_id=seed["host_id"],
        nfs_root=str(seed["log_root"]), force=True,
    )
    return uploader


class _Unavailable:
    status_code = 503
    text = "injected unavailable before receipt commit"


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.parametrize("explicit_state_db", [True, False], ids=["explicit", "main-agent-default"])
async def test_restart_revalidates_landed_destination_and_resends_verified(
    monkeypatch, tmp_path, explicit_state_db,
):
    seed = _seed(tmp_path, monkeypatch)
    if explicit_state_db:
        state_db = tmp_path / "agent_state.db"
        _create_state_table(state_db)
        monkeypatch.setenv("STP_AGENT_STATE_DB", str(state_db))
    else:
        monkeypatch.delenv("STP_AGENT_STATE_DB", raising=False)
        monkeypatch.setattr(event_uploader_module, "BASE_DIR", tmp_path)
        _create_state_table(tmp_path / "agent_state.db")

    uploader = _configure(EvidenceUploader(), seed)
    uploader_module._durable_set(seed["publication_id"], 4)
    monkeypatch.setattr(
        uploader_module.requests, "post", lambda *args, **kwargs: _Unavailable(),
    )
    task = _task(seed)
    uploader._upload(task)
    destination = seed["log_root"] / seed["destination_rel_path"]
    assert destination.is_dir()
    assert uploader_module._durable_get(seed["publication_id"]) == 5

    restarted = _configure(EvidenceUploader(), seed)
    refreshes = []
    monkeypatch.setattr(
        agent_evidence_route, "refresh_delivery_and_archive",
        lambda *args, **kwargs: refreshes.append((args, kwargs)),
    )
    monkeypatch.setattr(
        uploader_module, "copytree_no_symlinks",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not copy")),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://cp",
    ) as client:
        loop = asyncio.get_running_loop()

        def fake_post(url, json=None, headers=None, timeout=None):
            coro = client.post(url.replace("http://cp", "", 1), json=json, headers=headers)
            return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)

        monkeypatch.setattr(uploader_module.requests, "post", fake_post)
        await asyncio.to_thread(restarted._upload, _task(seed))

    db = SessionLocal()
    try:
        row = facts.get_evidence_publication(
            db, publication_id=seed["publication_id"],
        )
        assert row.state == facts.PUBLICATION_STATE_VERIFIED
        assert row.attempts == 5
        assert row.checksum == uploader_module.dir_sha256_and_size(
            Path(seed["local_path"])
        )[0]
    finally:
        db.close()
    assert len(refreshes) == 1
    assert (destination / "main.dbg").read_bytes() == b"dbg"
    assert uploader_module._durable_get(seed["publication_id"]) == 0