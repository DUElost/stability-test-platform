"""Script catalog API tests."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.tests.script_package_site import Site


def _uniq(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:8]}"


def _create_referenced_script(db_session, prefix: str) -> tuple[Script, Plan]:
    name = _uniq(prefix)
    script = Script(
        name=name,
        display_name=name,
        category="legacy",
        script_type="python",
        version="1.0.0",
        nfs_path=f"/nfs/scripts/{name}/1.0.0/{name}.py",
        content_sha256="b" * 64,
        param_schema={},
        default_params={},
        is_active=True,
    )
    db_session.add(script)
    db_session.flush()

    plan = Plan(
        name=_uniq("legacy_plan"),
        description="references legacy script",
        created_by="test",
    )
    db_session.add(plan)
    db_session.flush()

    db_session.add(PlanStep(
        plan_id=plan.id,
        step_key="patrol.legacy_script",
        script_name=script.name,
        script_version=script.version,
        stage="patrol",
        sort_order=0,
    ))
    db_session.commit()
    db_session.refresh(script)
    db_session.refresh(plan)
    return script, plan



def test_script_crud_and_soft_delete(client, admin_headers, auth_headers):
    name = _uniq("push_bundle")
    payload = {
        "name": name,
        "display_name": "Push Bundle",
        "category": "resource",
        "script_type": "python",
        "version": "1.0.0",
        "nfs_path": "/mnt/storage/test-platform/scripts/resource/push_bundle/v1.0.0/push_bundle.py",
        "content_sha256": "a" * 64,
        "param_schema": {"bundle_name": {"type": "string", "required": True}},
        "description": "Push resource bundle",
        "is_active": True,
    }

    create_resp = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
    assert create_resp.status_code == 201
    created = create_resp.json()["data"]
    assert created["name"] == name
    assert created["version"] == "1.0.0"
    assert created["is_active"] is True

    duplicate_resp = client.post("/api/v1/scripts", json=payload, headers=admin_headers)
    assert duplicate_resp.status_code == 409

    script_id = created["id"]
    update_resp = client.put(
        f"/api/v1/scripts/{script_id}",
        json={"display_name": "Push Bundle Updated", "is_active": False},
        headers=admin_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["data"]["display_name"] == "Push Bundle Updated"
    assert update_resp.json()["data"]["is_active"] is False

    get_resp = client.get(f"/api/v1/scripts/{script_id}", headers=auth_headers)
    assert get_resp.status_code == 200

    delete_resp = client.delete(f"/api/v1/scripts/{script_id}", headers=admin_headers)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["data"]["deactivated"] == script_id


def _site(tmp_path, monkeypatch, runtime_root="/opt/stability-test-agent/agent/scripts"):
    site = Site(tmp_path)
    for k, v in site.env(runtime_root).items():
        monkeypatch.setenv(k, v)
    return site


def test_script_scan_registers_from_packages_and_reports_missing(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    """ADR-0051 Phase 3：注册输入 = tool_manifest.json + 站点 packages/；未发布的条目只报告。"""
    site = _site(tmp_path, monkeypatch)
    site.add("connect_wifi", "1.0.0", {"connect_wifi.sh": "#!/usr/bin/env bash\necho wifi\n"}, script="connect_wifi.sh")
    site.add("connect_wifi", "1.0.1", {"connect_wifi.sh": "#!/usr/bin/env bash\necho v2\n"}, script="connect_wifi.sh", publish=False)

    first = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert first.status_code == 200
    data = first.json()["data"]
    assert (data["created"], data["skipped"], data["deactivated"], data["conflicts"]) == (1, 0, 0, [])
    assert [(m["name"], m["version"], m["reason"]) for m in data["package_missing"]] == [
        ("connect_wifi", "1.0.1", "package_missing")
    ]
    scripts = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers).json()["data"]
    assert [(s["name"], s["version"], s["script_type"], s["category"]) for s in scripts] == [
        ("connect_wifi", "1.0.0", "shell", "device")
    ]
    assert scripts[0]["nfs_path"] == "/opt/stability-test-agent/agent/scripts/connect_wifi/v1.0.0/connect_wifi.sh"
    assert scripts[0]["package_sha256"]

    second = client.post("/api/v1/scripts/scan", headers=admin_headers).json()["data"]
    assert (second["created"], second["skipped"]) == (0, 1)

    # 退役 = manifest retired:true（显式动作）；不再有「盘上缺失即反激活」
    site.retire("connect_wifi", "1.0.0")
    third = client.post("/api/v1/scripts/scan", headers=admin_headers).json()["data"]
    assert third["deactivated"] == 1
    assert [(d["name"], d["version"]) for d in third["deactivated_versions"]] == [("connect_wifi", "1.0.0")]
    assert client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers).json()["data"] == []


def test_script_scan_force_rebaseline_reanchors_drifted_row(
    client, tmp_path, monkeypatch, admin_headers, db_session
):
    site = _site(tmp_path, monkeypatch)
    site.add("connect_wifi", "1.0.0", {"connect_wifi.sh": "echo wifi\n"}, script="connect_wifi.sh")
    assert client.post("/api/v1/scripts/scan", headers=admin_headers).json()["data"]["created"] == 1
    row = db_session.query(Script).filter_by(name="connect_wifi", version="1.0.0").one()
    row.content_sha256 = "f" * 64  # 库侧漂移（包不可变）
    db_session.commit()

    plain = client.post("/api/v1/scripts/scan", headers=admin_headers).json()["data"]
    assert plain["conflicts"] == [{"name": "connect_wifi", "version": "1.0.0"}]
    forced = client.post("/api/v1/scripts/scan", params={"force_rebaseline": True}, headers=admin_headers).json()["data"]
    assert [(r["name"], r["version"]) for r in forced["rebaselined"]] == [("connect_wifi", "1.0.0")]
    db_session.refresh(row)
    assert row.content_sha256 != "f" * 64


def test_script_scan_force_rebaseline_refused_while_plan_run_in_flight(
    client, tmp_path, monkeypatch, admin_headers, sample_plan_run
):
    site = _site(tmp_path, monkeypatch)
    site.add("connect_wifi", "1.0.0", {"connect_wifi.sh": "echo wifi\n"}, script="connect_wifi.sh")
    assert client.post("/api/v1/scripts/scan", headers=admin_headers).status_code == 200
    assert sample_plan_run.status == "RUNNING"

    refused = client.post("/api/v1/scripts/scan", params={"force_rebaseline": True}, headers=admin_headers)
    assert refused.status_code == 409
    assert refused.json()["detail"]["code"] == "PLAN_RUN_IN_FLIGHT"

    # The plain scan path stays available while runs are in flight.
    plain = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert plain.status_code == 200 and plain.json()["data"]["skipped"] == 1


def test_script_scan_ignores_legacy_external_and_windows_batch_entries(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    site = _site(tmp_path, monkeypatch)
    site.add("scan_aee", "1.0.0", {"scan_aee.py": "legacy\n"})                       # legacy 名
    site.add("Start-Log-Scan", "2026.09.22", {"s.py": "x\n"}, script="s.py", python="venv/bin/python", kind="tool")  # 外部工具族（kind 驱动，ADR-0051 v1.3）
    site.add("win_tool", "1.0.0", {"win_tool.bat": "@echo off\n"}, script="win_tool.bat")  # .bat 不支持
    site.add("ok_tool", "1.0.0", {"ok_tool.py": "print(1)\n"})

    data = client.post("/api/v1/scripts/scan", headers=admin_headers).json()["data"]
    assert data["created"] == 1
    assert [(c["name"], c["reason"]) for c in data["package_conflicts"]] == [("win_tool", "package_entry_missing")]
    names = [s["name"] for s in client.get("/api/v1/scripts", headers=auth_headers).json()["data"]]
    assert names == ["ok_tool"]


def test_script_scan_requires_packages_root(client, monkeypatch, admin_headers, tmp_path):
    monkeypatch.setenv("STP_TOOL_MANIFEST", str(tmp_path / "tool_manifest.json"))
    monkeypatch.delenv("STP_PACKAGES_ROOT", raising=False)
    monkeypatch.delenv("STP_AEE_NFS_ROOT", raising=False)

    resp = client.post("/api/v1/scripts/scan", headers=admin_headers)

    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "PACKAGES_ROOT_NOT_CONFIGURED"


def test_script_endpoints_require_auth_and_admin_for_writes(client, admin_headers, auth_headers):
    list_resp = client.get("/api/v1/scripts")
    assert list_resp.status_code == 401

    list_authed = client.get("/api/v1/scripts", headers=auth_headers)
    assert list_authed.status_code == 200

    create_resp = client.post(
        "/api/v1/scripts",
        json={
            "name": _uniq("forbidden_script"),
            "script_type": "python",
            "version": "1.0.0",
            "nfs_path": "/tmp/forbidden.py",
            "content_sha256": "f" * 64,
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 403


def test_create_rejects_windows_batch_script_type(client, admin_headers):
    resp = client.post(
        "/api/v1/scripts",
        json={
            "name": _uniq("legacy_windows"),
            "display_name": "Legacy Windows Script",
            "category": "device",
            "script_type": "bat",
            "version": "1.0.0",
            "nfs_path": "/scripts/legacy_windows/v1.0.0/legacy_windows.bat",
            "content_sha256": "d" * 64,
            "default_params": {},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 422, resp.text
    assert "script_type" in resp.text


def test_create_rejects_legacy_aee_script_name(client, admin_headers):
    resp = client.post(
        "/api/v1/scripts",
        json={
            "name": "scan_aee",
            "display_name": "Legacy Scan AEE",
            "category": "device",
            "script_type": "python",
            "version": "1.0.0",
            "nfs_path": "/scripts/scan_aee/v1.0.0/scan_aee.py",
            "content_sha256": "1" * 64,
            "default_params": {},
        },
        headers=admin_headers,
    )

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == {
        "code": "LEGACY_AEE_SCRIPTS_DISABLED",
        "scripts": ["scan_aee:1.0.0"],
    }


def test_list_scripts_hides_legacy_aee_rows(
    client, auth_headers, db_session,
):
    visible_name = _uniq("visible_script")
    db_session.add_all([
        Script(
            name="scan_aee",
            display_name="Legacy Scan AEE",
            category="legacy-only",
            script_type="python",
            version="1.0.0",
            nfs_path="/scripts/scan_aee/v1.0.0/scan_aee.py",
            content_sha256="2" * 64,
            param_schema={},
            default_params={},
            is_active=True,
        ),
        Script(
            name=visible_name,
            display_name="Visible Script",
            category="device",
            script_type="python",
            version="1.0.0",
            nfs_path=f"/scripts/{visible_name}/v1.0.0/{visible_name}.py",
            content_sha256="3" * 64,
            param_schema={},
            default_params={},
            is_active=True,
        ),
    ])
    db_session.commit()

    resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)

    assert resp.status_code == 200
    names = {(item["name"], item["version"]) for item in resp.json()["data"]}
    assert ("scan_aee", "1.0.0") not in names
    assert (visible_name, "1.0.0") in names


def test_get_script_hides_legacy_aee_row(
    client, auth_headers, db_session,
):
    legacy = Script(
        name="scan_aee",
        display_name="Legacy Scan AEE",
        category="legacy-only",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/scan_aee/v1.0.0/scan_aee.py",
        content_sha256="2" * 64,
        param_schema={},
        default_params={},
        is_active=True,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    resp = client.get(f"/api/v1/scripts/{legacy.id}", headers=auth_headers)

    assert resp.status_code == 404, resp.text


def test_update_script_hides_legacy_aee_row(
    client, admin_headers, db_session,
):
    legacy = Script(
        name="scan_aee",
        display_name="Legacy Scan AEE",
        category="legacy-only",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/scan_aee/v1.0.0/scan_aee.py",
        content_sha256="2" * 64,
        param_schema={},
        default_params={},
        is_active=True,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    resp = client.put(
        f"/api/v1/scripts/{legacy.id}",
        json={"display_name": "Renamed Legacy"},
        headers=admin_headers,
    )

    assert resp.status_code == 404, resp.text


def test_delete_script_hides_legacy_aee_row(
    client, admin_headers, db_session,
):
    legacy = Script(
        name="export_mobilelogs",
        display_name="Legacy Export Mobilelogs",
        category="legacy-only",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/export_mobilelogs/v1.0.0/export_mobilelogs.py",
        content_sha256="4" * 64,
        param_schema={},
        default_params={},
        is_active=True,
    )
    db_session.add(legacy)
    db_session.commit()
    db_session.refresh(legacy)

    resp = client.delete(f"/api/v1/scripts/{legacy.id}", headers=admin_headers)

    assert resp.status_code == 404, resp.text


def test_list_script_categories_hides_legacy_aee_only_categories(
    client, auth_headers, db_session,
):
    visible_name = _uniq("visible_category_script")
    db_session.add_all([
        Script(
            name="export_mobilelogs",
            display_name="Legacy Export Mobilelogs",
            category="legacy-only",
            script_type="python",
            version="1.0.0",
            nfs_path="/scripts/export_mobilelogs/v1.0.0/export_mobilelogs.py",
            content_sha256="4" * 64,
            param_schema={},
            default_params={},
            is_active=True,
        ),
        Script(
            name=visible_name,
            display_name="Visible Category Script",
            category="device",
            script_type="python",
            version="1.0.0",
            nfs_path=f"/scripts/{visible_name}/v1.0.0/{visible_name}.py",
            content_sha256="5" * 64,
            param_schema={},
            default_params={},
            is_active=True,
        ),
    ])
    db_session.commit()

    resp = client.get("/api/v1/scripts/categories", headers=auth_headers)

    assert resp.status_code == 200
    categories = resp.json()["data"]
    assert "legacy-only" not in categories
    assert "device" in categories


def test_update_rejects_deactivation_when_script_is_still_referenced(
    client, admin_headers, db_session
):
    script, plan = _create_referenced_script(db_session, "scan_aee_ref")

    resp = client.put(
        f"/api/v1/scripts/{script.id}",
        json={"is_active": False},
        headers=admin_headers,
    )

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "SCRIPT_STILL_REFERENCED"
    assert detail["script"] == f"{script.name}:{script.version}"
    assert detail["plan_ids"] == [plan.id]

    db_session.refresh(script)
    assert script.is_active is True


def test_update_rejects_contract_field_changes(client, admin_headers, db_session):
    """#790：name/version/nfs_path/content_sha256 不可原地改（ADR-0020/0021 D9）。

    否则可绕过 force_rebaseline 的在途 PlanRun 守卫，或让 PlanStep 的
    (name, version) 引用键失配（2026-07-31 事故同型）。"""
    script, _ = _create_referenced_script(db_session, "contract_guard")

    for field, new_value in (
        ("name", script.name + "_renamed"),
        ("version", "9.9.9"),
        ("nfs_path", "/nfs/scripts/elsewhere/9.9.9/evil.py"),
        ("content_sha256", "c" * 64),
    ):
        resp = client.put(
            f"/api/v1/scripts/{script.id}",
            json={field: new_value},
            headers=admin_headers,
        )
        assert resp.status_code == 422, (field, resp.text)
        assert field in resp.json()["detail"], (field, resp.text)

    db_session.refresh(script)
    assert script.version == "1.0.0"
    assert script.content_sha256 == "b" * 64


def test_update_allows_unchanged_contract_fields(client, admin_headers, db_session):
    """客户端回传全量对象（契约字段同值）+ 展示字段时不得被 422 误伤。"""
    script, _ = _create_referenced_script(db_session, "contract_noop")

    resp = client.put(
        f"/api/v1/scripts/{script.id}",
        json={
            "name": script.name,
            "version": script.version,
            "nfs_path": script.nfs_path,
            "content_sha256": script.content_sha256,
            "display_name": "Renamed Display",
            "description": "guard test",
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["display_name"] == "Renamed Display"
    assert data["description"] == "guard test"


def test_delete_rejects_deactivation_when_script_is_still_referenced(
    client, admin_headers, db_session
):
    script, plan = _create_referenced_script(db_session, "mobilelogs_ref")

    resp = client.delete(f"/api/v1/scripts/{script.id}", headers=admin_headers)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "SCRIPT_STILL_REFERENCED"
    assert detail["script"] == f"{script.name}:{script.version}"
    assert detail["plan_ids"] == [plan.id]

    db_session.refresh(script)
    assert script.is_active is True




def _usage_snapshot_steps(
    script_name: str,
    script_version: str,
) -> dict:
    return {
        "steps": [
            {
                "stage": "init",
                "step_key": "s1",
                "script_name": script_name,
                "script_version": script_version,
            },
        ],
    }


def test_script_usage_aggregates_by_project(client, auth_headers, db_session, sample_device):
    """ADR-0029 P2-10：plan_snapshot 执行事实 + plan_step 配置引用。"""
    from backend.models.plan import Plan, PlanStep
    from backend.models.plan_run import PlanRun
    from backend.models.project import TestProject
    from backend.models.script import Script

    script = Script(
        name="usage_probe", script_type="python", version="1.0.0",
        nfs_path="/s/usage_probe.py", content_sha256="u" * 64,
        default_params={}, param_schema={}, is_active=True,
    )
    db_session.add(script)
    db_session.commit()

    proj_a = TestProject(project_key="USG-A", display_name="a", source="USER")
    proj_b = TestProject(project_key="USG-B", display_name="b", source="USER")
    db_session.add_all([proj_a, proj_b])
    db_session.commit()

    plan_a = Plan(name="usg-plan-a", project_id=proj_a.id)
    plan_b = Plan(name="usg-plan-b", project_id=proj_b.id)
    db_session.add_all([plan_a, plan_b])
    db_session.commit()
    db_session.add_all([
        PlanStep(plan_id=plan_a.id, step_key="s1", script_name="usage_probe",
                 script_version="1.0.0", stage="init", sort_order=0),
        PlanStep(plan_id=plan_b.id, step_key="s1", script_name="usage_probe",
                 script_version="1.0.0", stage="init", sort_order=0),
    ])
    db_session.commit()
    snap = _usage_snapshot_steps("usage_probe", "1.0.0")
    now = datetime.now(timezone.utc)
    db_session.add_all([
        PlanRun(plan_id=plan_a.id, project_id=proj_a.id, status="SUCCESS",
                plan_snapshot=snap, run_type="MANUAL", triggered_by="t",
                started_at=now - timedelta(days=1)),
        PlanRun(plan_id=plan_a.id, project_id=proj_a.id, status="FAILED",
                plan_snapshot=snap, run_type="MANUAL", triggered_by="t",
                started_at=now - timedelta(days=1)),
        PlanRun(plan_id=plan_b.id, project_id=proj_b.id, status="SUCCESS",
                plan_snapshot=snap, run_type="MANUAL", triggered_by="t",
                started_at=now - timedelta(days=1)),
    ])
    db_session.commit()

    resp = client.get(f"/api/v1/scripts/{script.id}/usage", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    by_key = {p["project_key"]: p for p in data["projects"]}
    assert by_key["USG-A"]["run_count"] == 2
    assert by_key["USG-A"]["success_count"] == 1
    assert by_key["USG-A"]["success_rate"] == 0.5
    assert by_key["USG-A"]["plan_count"] == 1
    assert by_key["USG-A"]["versions_used"] == [{
        "script_version": "1.0.0",
        "run_count": 2,
        "success_count": 1,
        "success_rate": 0.5,
    }]
    assert by_key["USG-B"]["run_count"] == 1
    assert by_key["USG-B"]["success_rate"] == 1.0


def test_script_usage_config_ref_without_recent_runs(client, auth_headers, db_session):
    """退役交叉：配置有引用 + 窗口内零执行 → plan_count>0, run_count=0。"""
    from backend.models.plan import Plan, PlanStep
    from backend.models.project import TestProject
    from backend.models.script import Script

    script = Script(
        name="retire_cfg_only", script_type="python", version="1.0.0",
        nfs_path="/s/retire.py", content_sha256="r" * 64,
        default_params={}, param_schema={}, is_active=True,
    )
    proj = TestProject(project_key="RET-CFG", display_name="cfg", source="USER")
    db_session.add_all([script, proj])
    db_session.commit()
    plan = Plan(name="retire-plan", project_id=proj.id)
    db_session.add(plan)
    db_session.commit()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s1", script_name="retire_cfg_only",
        script_version="1.0.0", stage="init", sort_order=0,
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/scripts/{script.id}/usage", headers=auth_headers)
    assert resp.status_code == 200
    row = resp.json()["data"]["projects"][0]
    assert row["project_key"] == "RET-CFG"
    assert row["plan_count"] == 1
    assert row["run_count"] == 0
    assert row["versions_used"] == []


def test_script_usage_runs_without_config_ref(client, auth_headers, db_session):
    """退役交叉：配置零引用 + 窗口内有执行 → 仍出现且 versions_used 可查。"""
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    from backend.models.project import TestProject
    from backend.models.script import Script

    script = Script(
        name="retire_run_only", script_type="python", version="1.0.0",
        nfs_path="/s/retire_run.py", content_sha256="x" * 64,
        default_params={}, param_schema={}, is_active=True,
    )
    proj = TestProject(project_key="RET-RUN", display_name="run", source="USER")
    db_session.add_all([script, proj])
    db_session.commit()
    plan = Plan(name="orphan-plan", project_id=proj.id)
    db_session.add(plan)
    db_session.commit()
    now = datetime.now(timezone.utc)
    db_session.add(PlanRun(
        plan_id=plan.id, project_id=proj.id, status="SUCCESS",
        plan_snapshot=_usage_snapshot_steps("retire_run_only", "1.0.0"),
        run_type="MANUAL", triggered_by="t",
        started_at=now - timedelta(days=1),
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/scripts/{script.id}/usage", headers=auth_headers)
    assert resp.status_code == 200
    row = resp.json()["data"]["projects"][0]
    assert row["project_key"] == "RET-RUN"
    assert row["plan_count"] == 0
    assert row["run_count"] == 1
    assert row["versions_used"] == [{
        "script_version": "1.0.0",
        "run_count": 1,
        "success_count": 1,
        "success_rate": 1.0,
    }]


def test_script_usage_versions_used_when_config_diverges(client, auth_headers, db_session):
    """plan_step 已改版本，快照仍记录旧版执行。"""
    from backend.models.plan import Plan, PlanStep
    from backend.models.plan_run import PlanRun
    from backend.models.project import TestProject
    from backend.models.script import Script

    script = Script(
        name="version_drift", script_type="python", version="1.0.0",
        nfs_path="/s/drift.py", content_sha256="d" * 64,
        default_params={}, param_schema={}, is_active=True,
    )
    proj = TestProject(project_key="DRIFT", display_name="d", source="USER")
    db_session.add_all([script, proj])
    db_session.commit()
    plan = Plan(name="drift-plan", project_id=proj.id)
    db_session.add(plan)
    db_session.commit()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="s1", script_name="version_drift",
        script_version="2.0.0", stage="init", sort_order=0,
    ))
    now = datetime.now(timezone.utc)
    db_session.add(PlanRun(
        plan_id=plan.id, project_id=proj.id, status="SUCCESS",
        plan_snapshot=_usage_snapshot_steps("version_drift", "1.0.0"),
        run_type="MANUAL", triggered_by="t",
        started_at=now - timedelta(days=1),
    ))
    db_session.commit()

    resp = client.get(f"/api/v1/scripts/{script.id}/usage", headers=auth_headers)
    assert resp.status_code == 200
    row = resp.json()["data"]["projects"][0]
    assert row["plan_count"] == 0
    assert row["run_count"] == 1
    assert row["versions_used"][0]["script_version"] == "1.0.0"


# ── #1026（R08-F06）：脚本版本创建的 SHA 与路径契约 ───────────────────────


class TestScriptVersionCreateValidation:
    """POST /{name}/versions：空/非法 SHA 与复用旧版本路径必须 422 拒绝。"""

    def _seed_active_script(self, client, admin_headers, name: str) -> None:
        resp = client.post("/api/v1/scripts", json={
            "name": name,
            "display_name": name,
            "category": "device",
            "script_type": "python",
            "version": "1.0.0",
            "nfs_path": f"/scripts/{name}/v1.0.0/{name}.py",
            "content_sha256": "a" * 64,
            "default_params": {},
        }, headers=admin_headers)
        assert resp.status_code in (200, 201), resp.text

    def _post_version(self, client, admin_headers, name: str, **overrides) -> object:
        payload = {
            "version": "2.0.0",
            "nfs_path": f"/scripts/{name}/v2.0.0/{name}.py",
            "content_sha256": "b" * 64,
            "param_schema": {},
            "default_params": {},
        }
        payload.update(overrides)
        return client.post(f"/api/v1/scripts/{name}/versions", json=payload,
                           headers=admin_headers)

    def test_empty_sha_rejected(self, client, admin_headers):
        name = _uniq("sha_val")
        self._seed_active_script(client, admin_headers, name)
        resp = self._post_version(client, admin_headers, name, content_sha256="  ")
        assert resp.status_code == 422, resp.text
        assert "content_sha256" in str(resp.json())

    def test_non_hex_short_sha_rejected(self, client, admin_headers):
        name = _uniq("sha_val")
        self._seed_active_script(client, admin_headers, name)
        resp = self._post_version(client, admin_headers, name, content_sha256="abc123")
        assert resp.status_code == 422, resp.text
        assert "content_sha256" in str(resp.json())

    def test_reusing_old_version_path_rejected(self, client, admin_headers):
        name = _uniq("sha_val")
        self._seed_active_script(client, admin_headers, name)
        resp = self._post_version(
            client, admin_headers, name,
            nfs_path=f"/scripts/{name}/v1.0.0/{name}.py",  # 静默复用旧版本路径
        )
        assert resp.status_code == 422, resp.text
        assert "v2.0.0" in str(resp.json())

    def test_valid_sha_and_matching_path_created(self, client, admin_headers):
        name = _uniq("sha_val")
        self._seed_active_script(client, admin_headers, name)
        resp = self._post_version(client, admin_headers, name)
        assert resp.status_code == 201, resp.text
        data = resp.json()["data"]
        assert data["version"] == "2.0.0"
        assert data["is_active"] is True


def test_script_usage_versions_drilldown(client, auth_headers, db_session):
    """#706：版本级执行事实——每个版本被哪些项目跑过、各自 run 数与成功率。"""
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun
    from backend.models.project import TestProject
    from backend.models.script import Script

    script = Script(
        name="usage_versions", script_type="python", version="1.0.0",
        nfs_path="/s/usage_versions.py", content_sha256="v" * 64,
        default_params={}, param_schema={}, is_active=True,
    )
    db_session.add(script)
    db_session.commit()

    proj_a = TestProject(project_key="USV-A", display_name="a", source="USER")
    proj_b = TestProject(project_key="USV-B", display_name="b", source="USER")
    db_session.add_all([proj_a, proj_b])
    db_session.commit()

    plan_a = Plan(name="usv-plan-a", project_id=proj_a.id)
    plan_b = Plan(name="usv-plan-b", project_id=proj_b.id)
    db_session.add_all([plan_a, plan_b])
    db_session.commit()

    now = datetime.now(timezone.utc)
    rows = [
        (proj_a, plan_a, "1.0.0", "SUCCESS"),
        (proj_a, plan_a, "1.0.0", "FAILED"),
        (proj_b, plan_b, "1.0.0", "SUCCESS"),
        (proj_b, plan_b, "1.0.1", "FAILED"),
    ]
    db_session.add_all([
        PlanRun(
            plan_id=plan.id, project_id=proj.id, status=status,
            plan_snapshot=_usage_snapshot_steps("usage_versions", version),
            run_type="MANUAL", triggered_by="t", started_at=now - timedelta(days=1),
        )
        for proj, plan, version, status in rows
    ])
    db_session.commit()

    resp = client.get(f"/api/v1/scripts/{script.id}/usage", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    by_version = {v["script_version"]: v for v in data["versions"]}
    assert set(by_version) == {"1.0.0", "1.0.1"}

    v100 = by_version["1.0.0"]
    assert v100["run_count"] == 3
    assert v100["success_count"] == 2
    assert v100["success_rate"] == 0.67
    assert v100["project_count"] == 2
    # 版本内按 run_count 降序
    assert [p["project_key"] for p in v100["projects"]] == ["USV-A", "USV-B"]

    v101 = by_version["1.0.1"]
    assert v101["run_count"] == 1
    assert v101["success_count"] == 0
    assert v101["projects"] == [{
        "project_key": "USV-B", "run_count": 1, "success_count": 0, "success_rate": 0.0,
    }]

    # 版本间按总 run_count 降序
    assert data["versions"][0]["script_version"] == "1.0.0"
