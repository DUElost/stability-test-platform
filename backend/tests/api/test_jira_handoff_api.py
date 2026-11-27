"""B11-U1（#4199 / #4174）REST API 端点离线契约测试（不依赖外部 PG）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.auth import get_current_active_user
from backend.api.routes.jira_handoff import router as jira_handoff_router
from backend.core.database import get_db
from backend.tests.services.test_jira_handoff_b11_u1 import (
    _create_stub_dedup_org_tool,
    _setup_base_db,
    _write_mapping_json,
    _write_org_xls,
)


def _build_app(db, *, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(jira_handoff_router)
    app.dependency_overrides[get_db] = lambda: db
    if authed:
        app.dependency_overrides[get_current_active_user] = lambda: SimpleNamespace(
            id=7, username="qa_owner", role="admin", is_active=True,
        )
    return app


def test_jira_handoff_api_unauthenticated_rejected():
    db = _setup_base_db(run_id=201)
    client = TestClient(_build_app(db, authed=False))
    resp = client.post("/api/v1/jira/handoff/preflight", json={"plan_run_ids": [201]})
    assert resp.status_code == 401


def test_jira_handoff_api_end_to_end_and_error_codes(tmp_path: Path, monkeypatch):
    t0 = datetime(2026, 11, 20, 10, 0, 0, tzinfo=timezone.utc)
    db = _setup_base_db(run_id=201, hosts=("host-a", "host-b"), started_at=t0)
    dle1 = uuid4()
    db.dles.append(
        SimpleNamespace(
            id=dle1, serial="SN001", platform="mtk", plan_run_id=201, host_id="host-a",
            occurrence_key="occ-api-1", detected_at=t0 + timedelta(minutes=15),
            local_path="/hdd/SN001/aee_exp/e1", remote_path=None,
        )
    )
    org_a = tmp_path / "dedup" / "201" / "mtk" / "host-a_Result_org.xls"
    row_path = "/s/SN001/aee_exp/e1/d.DEC/__exp_main.txt"
    _write_org_xls(org_a, [{"Path": row_path, "DeviceId": "SN001", "CausedBy": "ApiCrash"}])
    _write_mapping_json(org_a, [{"sheet": "aeeexp", "row": 2, "path": row_path, "mapped": True, "dle_id": str(dle1), "occurrence_key": "occ-api-1"}])
    db.artifacts.append(
        SimpleNamespace(id=1, plan_run_id=201, host_id="host-a", storage_uri=str(org_a), artifact_type="scan_result_xls", created_at=t0 + timedelta(hours=1))
    )

    storage = tmp_path / "center"
    tool = _create_stub_dedup_org_tool(tmp_path)
    monkeypatch.setenv("STP_AEE_NFS_ROOT", str(storage))
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_PYTHON", tool["python"])
    monkeypatch.setenv("STP_BACKEND_DEDUP_SCAN_SCRIPT", tool["script"])

    client = TestClient(_build_app(db, authed=True))

    # 1) platform="unisoc" → 400
    r_bad_plat = client.post(
        "/api/v1/jira/handoff/preflight",
        json={"plan_run_ids": [201], "platform": "unisoc"},
    )
    assert r_bad_plat.status_code == 400
    assert r_bad_plat.json()["detail"]["code"] == "handoff_unsupported_platform"

    # 2) preflight → 200 且暴露缺失 host-b
    r_pre = client.post(
        "/api/v1/jira/handoff/preflight",
        json={"plan_run_ids": [201], "platform": "mtk"},
    )
    assert r_pre.status_code == 200
    pre_data = r_pre.json()["data"]
    assert pre_data["gates"]["coverage_complete"] is False
    assert pre_data["runs"][0]["jira_project_key"] == "MLD"

    # 3) 默认创建被覆盖率门禁拦住 → 409
    r_409 = client.post(
        "/api/v1/jira/handoff/bundles",
        json={"plan_run_ids": [201], "platform": "mtk"},
    )
    assert r_409.status_code == 409
    assert r_409.json()["detail"]["code"] == "handoff_coverage_incomplete"

    # 4) 显式 allow_incomplete_coverage=True → 200 生成 PENDING_EXPORT 交付包
    r_ok = client.post(
        "/api/v1/jira/handoff/bundles",
        json={"plan_run_ids": [201], "platform": "mtk", "allow_incomplete_coverage": True},
    )
    assert r_ok.status_code == 200
    bundle = r_ok.json()["data"]
    handoff_id = bundle["handoff_id"]
    assert bundle["status"] == "PENDING_EXPORT"
    assert bundle["manifest_sha256"]

    # 5) 列表 + 详情 + 三类安全下载
    r_list = client.get("/api/v1/jira/handoff/bundles")
    assert r_list.status_code == 200
    assert any(item["handoff_id"] == handoff_id for item in r_list.json()["data"])

    r_detail = client.get(f"/api/v1/jira/handoff/bundles/{handoff_id}")
    assert r_detail.status_code == 200
    assert r_detail.json()["data"]["handoff_id"] == handoff_id

    for kind in ("manifest", "preview_xls", "review_workbook"):
        r_dl = client.get(f"/api/v1/jira/handoff/bundles/{handoff_id}/files/{kind}")
        assert r_dl.status_code == 200
        assert len(r_dl.content) > 0

    r_bad_kind = client.get(f"/api/v1/jira/handoff/bundles/{handoff_id}/files/secret")
    assert r_bad_kind.status_code == 400
