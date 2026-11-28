"""ADR-0067 U2 HTTP API 契约测试：草稿投影 project_key、v2 确认指纹、#2649 预览/派发与 L2/L3 投影。"""

from __future__ import annotations

from backend.models.enums import HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_run import PlanRun
from backend.models.project import TestProject
from backend.models.project_model import ProjectModel
from backend.models.script import Script
from backend.services.plan_dispatcher_core import build_lifecycle_from_snapshot


def _seed_log_prepare_110(db_session) -> Script:
    row = (
        db_session.query(Script)
        .filter_by(name="log_prepare", version="1.1.0")
        .one_or_none()
    )
    if row is None:
        row = Script(
            name="log_prepare",
            display_name="log_prepare",
            category="device",
            script_type="python",
            version="1.1.0",
            nfs_path="/opt/stability-test-agent/agent/scripts/log_prepare/v1.1.0/log_prepare.py",
            content_sha256="96e59b4d719ec5047e59de194bc7366d4d5bc17a3c0c011c1096db004928de3d",
            param_schema={
                "mobilelog_size_mb": {"type": "integer", "default": 4096, "minimum": 256, "maximum": 20480},
                "mtk_auto_start": {"type": "boolean", "default": True},
                "aee_mode": {"type": "string", "nullable": True, "pattern": "^[0-9]{1,2}$"},
                "ylog_size_mb": {"type": "integer", "default": 4096, "minimum": 64, "maximum": 10240},
                "ylog_rotate": {"type": "boolean", "default": True},
                "ylog_required_streams": {
                    "type": "array",
                    "default": ["android", "kernel"],
                    "minItems": 1,
                    "items": {"type": "string", "enum": ["android", "kernel", "uboot", "lastlog"]},
                    "required_items": ["android", "kernel"],
                },
            },
            default_params={
                "mobilelog_size_mb": 4096,
                "mtk_auto_start": True,
                "ylog_size_mb": 4096,
                "ylog_rotate": True,
                "ylog_required_streams": ["android", "kernel"],
            },
            is_active=True,
        )
        db_session.add(row)
        db_session.commit()
    return row


def _step_payload(params=None):
    return [{
        "step_key": "log_prepare",
        "script_name": "log_prepare",
        "script_version": "1.1.0",
        "stage": "init",
        "params": params if params is not None else {},
    }]


def test_draft_projection_project_key_switch_clear_and_inactive_guard(
    client, auth_headers, db_session,
):
    _seed_log_prepare_110(db_session)
    proj_a = TestProject(
        project_key="u2-api-a",
        display_name="API A",
        source="USER",
        status="ACTIVE",
        log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048},
        log_profile_version=1,
    )
    proj_b = TestProject(
        project_key="u2-api-b",
        display_name="API B",
        source="USER",
        status="ACTIVE",
        log_profile={"aee_mode": "4", "mobilelog_size_mb": 8192},
        log_profile_version=2,
    )
    proj_arch = TestProject(
        project_key="u2-api-arch",
        display_name="API Arch",
        source="USER",
        status="ARCHIVED",
        log_profile={"aee_mode": "3"},
        log_profile_version=1,
    )
    proj_seed = TestProject(
        project_key="u2-api-seed",
        display_name="API Seed",
        source="SEED",
        status="ACTIVE",
        log_profile={"aee_mode": "3"},
        log_profile_version=1,
    )
    db_session.add_all([proj_a, proj_b, proj_arch, proj_seed])
    db_session.commit()

    # 1) 选项目 A → pending_devices + project_preset 预估
    r_a = client.post(
        "/api/v1/plans/parameter-projection",
        headers=auth_headers,
        json={"project_key": "u2-api-a", "steps": _step_payload()},
    )
    assert r_a.status_code == 200, r_a.text
    d_a = r_a.json()["data"]
    assert d_a["project_log_profile"]["status"] == "pending_devices"
    assert d_a["project_log_profile"]["project_key"] == "u2-api-a"
    assert d_a["project_log_profile"]["log_profile_version"] == 1
    items_a = {tuple(item["path"]): item for item in d_a["steps"][0]["params"]}
    assert items_a[("mobilelog_size_mb",)]["value"] == 2048
    assert items_a[("mobilelog_size_mb",)]["source"] == "project_preset"
    assert items_a[("mobilelog_size_mb",)]["authority"] == "project_preset:u2-api-a@v1"
    assert any(
        dec["kind"] == "project_log_profile" and dec["state"] == "pending_dispatch"
        for dec in d_a["dispatch_decisions"]
    )

    # 2) 切换项目 B
    r_b = client.post(
        "/api/v1/plans/parameter-projection",
        headers=auth_headers,
        json={"project_key": "u2-api-b", "steps": _step_payload()},
    )
    assert r_b.status_code == 200, r_b.text
    d_b = r_b.json()["data"]
    assert d_b["project_log_profile"]["status"] == "pending_devices"
    items_b = {tuple(item["path"]): item for item in d_b["steps"][0]["params"]}
    assert items_b[("mobilelog_size_mb",)]["value"] == 8192
    assert items_b[("aee_mode",)]["value"] == "4"

    # 3) 清空项目（None / 空白字符串）→ skipped_unbound_plan 回落 4096 script_default
    for empty_key in (None, "   "):
        r_none = client.post(
            "/api/v1/plans/parameter-projection",
            headers=auth_headers,
            json={"project_key": empty_key, "steps": _step_payload()},
        )
        assert r_none.status_code == 200, r_none.text
        d_none = r_none.json()["data"]
        assert d_none["project_log_profile"]["status"] == "skipped_unbound_plan"
        items_none = {tuple(item["path"]): item for item in d_none["steps"][0]["params"]}
        assert items_none[("mobilelog_size_mb",)]["value"] == 4096
        assert items_none[("mobilelog_size_mb",)]["source"] == "script_default"

    # 4) 不存在 / SEED / ARCHIVED project_key 必须返回 200 skipped_project_inactive，不中断编辑
    for bad_key in ("nonexistent-key", "u2-api-arch", "u2-api-seed"):
        r_bad = client.post(
            "/api/v1/plans/parameter-projection",
            headers=auth_headers,
            json={"project_key": bad_key, "steps": _step_payload()},
        )
        assert r_bad.status_code == 200, r_bad.text
        assert r_bad.json()["data"]["project_log_profile"]["status"] == "skipped_project_inactive"

    # 5) extra="forbid" 仍生效
    r_extra = client.post(
        "/api/v1/plans/parameter-projection",
        headers=auth_headers,
        json={"project_key": "u2-api-a", "unknown_field": 1, "steps": _step_payload()},
    )
    assert r_extra.status_code == 422


def test_saved_projection_preview_run_v2_and_l2_l3_http_flow(
    client, auth_headers, db_session,
):
    _seed_log_prepare_110(db_session)
    proj = TestProject(
        project_key="u2-e2e-proj",
        display_name="E2E",
        source="USER",
        status="ACTIVE",
        log_profile={"aee_mode": "3", "mobilelog_size_mb": 2048},
        log_profile_version=1,
    )
    db_session.add(proj)
    db_session.flush()
    db_session.add(ProjectModel(project_id=proj.id, match_value="E2E_MODEL", is_active=True))
    host = Host(id="h-e2e", hostname="h-e2e", status=HostStatus.ONLINE.value)
    db_session.add(host)
    db_session.flush()
    dev_ok = Device(serial="SN-E2E-OK", host_id="h-e2e", model="E2E_MODEL", status="ONLINE")
    dev_sc = Device(serial="0123456789ABCDEF", host_id="h-e2e", model="OTHER_MODEL", status="ONLINE")
    db_session.add_all([dev_ok, dev_sc])
    plan = Plan(name="u2-e2e-plan", project_id=proj.id, created_by="tester")
    db_session.add(plan)
    db_session.flush()
    db_session.add(
        PlanStep(
            plan_id=plan.id,
            step_key="log_prepare",
            script_name="log_prepare",
            script_version="1.1.0",
            stage="init",
            sort_order=0,
            params={},
            enabled=True,
        )
    )
    db_session.commit()

    # GET /plans/{id}/parameter-projection → pending_devices
    saved = client.get(f"/api/v1/plans/{plan.id}/parameter-projection", headers=auth_headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["project_log_profile"]["status"] == "pending_devices"

    # 旧 stp-l1-v1 被 API 422 拒绝并提示 stp-l1-v2
    v1_resp = client.post(
        f"/api/v1/plans/{plan.id}/run",
        headers=auth_headers,
        json={"device_ids": [dev_ok.id], "confirmation_fingerprint": "stp-l1-v1:" + "a" * 64},
    )
    assert v1_resp.status_code == 422
    assert "stp-l1-v2:" in v1_resp.text

    # Preview 含 #2649 正常设备 + 冲突设备
    prev = client.post(
        f"/api/v1/plans/{plan.id}/run/preview",
        headers=auth_headers,
        json={"device_ids": [dev_ok.id, dev_sc.id]},
    )
    assert prev.status_code == 200, prev.text
    pdata = prev.json()["data"]
    assert pdata["device_ids"] == [dev_ok.id, dev_sc.id]
    assert pdata["project_log_profile"]["status"] == "applied"
    assert pdata["project_log_profile"]["evaluated_device_ids"] == [dev_ok.id]
    assert pdata["project_log_profile"]["excluded_serial_conflict_device_ids"] == [dev_sc.id]
    token = pdata["confirmation_fingerprint"]
    assert token.startswith("stp-l1-v2:")

    # 使用同一选择与 v2 指纹派发成功
    run_resp = client.post(
        f"/api/v1/plans/{plan.id}/run",
        headers=auth_headers,
        json={"device_ids": [dev_ok.id, dev_sc.id], "confirmation_fingerprint": token},
    )
    assert run_resp.status_code == 200, run_resp.text
    run_id = run_resp.json()["data"]["id"]

    # L2 detail 投影包含 project_log_profile 与 project_preset 来源
    detail = client.get(f"/api/v1/plan-runs/{run_id}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    l2 = detail.json()["data"]["parameter_projection"]
    assert l2["project_log_profile"]["status"] == "applied"
    l2_items = {tuple(item["path"]): item for item in l2["steps"][0]["params"]}
    assert l2_items[("mobilelog_size_mb",)]["source"] == "project_preset"
    assert l2_items[("mobilelog_size_mb",)]["value"] == 2048

    # L3 job 投影包含 project_preset 来源与 project_log_profile
    pr = db_session.get(PlanRun, run_id)
    assert pr is not None
    job = JobInstance(
        plan_run_id=pr.id,
        plan_id=plan.id,
        device_id=dev_ok.id,
        host_id="h-e2e",
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": build_lifecycle_from_snapshot(pr.plan_snapshot)},
    )
    db_session.add(job)
    db_session.commit()

    l3_resp = client.get(
        f"/api/v1/plan-runs/{run_id}/jobs/{job.id}/parameter-projection",
        headers=auth_headers,
    )
    assert l3_resp.status_code == 200, l3_resp.text
    l3 = l3_resp.json()["data"]
    assert l3["project_log_profile"]["status"] == "applied"
    l3_items = {tuple(item["path"]): item for item in l3["steps"][0]["params"]}
    assert l3_items[("aee_mode",)]["source"] == "project_preset"
    assert l3_items[("aee_mode",)]["value"] == "3"

    # 修改 log_profile 后使用旧 v2 指纹派发返回 409 PLAN_CONFIRMATION_CHANGED
    proj.log_profile = {"aee_mode": "4", "mobilelog_size_mb": 2048}
    proj.log_profile_version = 2
    db_session.commit()
    drift = client.post(
        f"/api/v1/plans/{plan.id}/run",
        headers=auth_headers,
        json={"device_ids": [dev_ok.id, dev_sc.id], "confirmation_fingerprint": token},
    )
    assert drift.status_code == 409
    assert drift.json()["detail"]["code"] == "PLAN_CONFIRMATION_CHANGED"
