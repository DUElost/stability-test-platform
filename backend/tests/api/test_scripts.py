"""Script catalog API tests."""

from uuid import uuid4

from backend.models.plan import Plan, PlanStep
from backend.models.script import Script


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
        failure_threshold=0.1,
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


def test_script_scan_registers_conflicts_and_deactivates_missing(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    root = tmp_path / "scripts"
    version_dir = root / "connect_wifi" / "v1.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "connect_wifi.sh"
    entry.write_text("#!/usr/bin/env bash\necho wifi\n", encoding="utf-8")

    monkeypatch.setenv("STP_SCRIPT_ROOT", str(root))

    first_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert first_scan.status_code == 200
    first_data = first_scan.json()["data"]
    assert first_data["created"] == 1
    assert first_data["skipped"] == 0
    assert first_data["deactivated"] == 0
    assert first_data["conflicts"] == []

    list_resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert list_resp.status_code == 200
    scripts = list_resp.json()["data"]
    assert len(scripts) == 1
    assert scripts[0]["name"] == "connect_wifi"
    assert scripts[0]["category"] == "device"
    assert scripts[0]["version"] == "1.0.0"
    assert scripts[0]["script_type"] == "shell"

    second_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert second_scan.status_code == 200
    assert second_scan.json()["data"]["skipped"] == 1

    entry.write_text("#!/usr/bin/env bash\necho changed\n", encoding="utf-8")
    conflict_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert conflict_scan.status_code == 200
    conflicts = conflict_scan.json()["data"]["conflicts"]
    assert conflicts == [{"name": "connect_wifi", "version": "1.0.0"}]

    entry.unlink()
    inactive_scan = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert inactive_scan.status_code == 200
    assert inactive_scan.json()["data"]["deactivated"] == 1

    inactive_list = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert inactive_list.status_code == 200
    assert inactive_list.json()["data"] == []


def test_script_scan_maps_source_root_to_agent_runtime_root(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    root = tmp_path / "agent" / "scripts"
    version_dir = root / "connect_wifi" / "v1.0.0"
    version_dir.mkdir(parents=True)
    entry = version_dir / "connect_wifi.sh"
    entry.write_text("#!/usr/bin/env bash\necho wifi\n", encoding="utf-8")

    monkeypatch.setenv("STP_SCRIPT_ROOT", str(root))
    monkeypatch.setenv("STP_SCRIPT_RUNTIME_ROOT", "/opt/stability-test-agent/agent/scripts")

    scan_resp = client.post("/api/v1/scripts/scan", headers=admin_headers)
    assert scan_resp.status_code == 200

    list_resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert list_resp.status_code == 200
    scripts = list_resp.json()["data"]
    assert len(scripts) == 1
    assert (
        scripts[0]["nfs_path"]
        == "/opt/stability-test-agent/agent/scripts/connect_wifi/v1.0.0/connect_wifi.sh"
    )


def test_script_scan_ignores_legacy_aee_script_directories(
    client, tmp_path, monkeypatch, admin_headers, auth_headers
):
    root = tmp_path / "scripts"
    legacy_dir = root / "scan_aee" / "v1.0.0"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "scan_aee.py").write_text("print('legacy')\n", encoding="utf-8")

    monkeypatch.setenv("STP_SCRIPT_ROOT", str(root))

    scan_resp = client.post("/api/v1/scripts/scan", headers=admin_headers)

    assert scan_resp.status_code == 200
    data = scan_resp.json()["data"]
    assert data["created"] == 0
    assert data["skipped"] == 0
    assert data["deactivated"] == 0
    assert data["conflicts"] == []

    list_resp = client.get("/api/v1/scripts", params={"is_active": True}, headers=auth_headers)
    assert list_resp.status_code == 200
    assert all(item["name"] != "scan_aee" for item in list_resp.json()["data"])


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


def test_script_scan_missing_root_returns_structured_error(
    client, monkeypatch, admin_headers, tmp_path
):
    missing_root = tmp_path / "missing-script-root"
    monkeypatch.setenv("STP_SCRIPT_ROOT", str(missing_root))

    resp = client.post("/api/v1/scripts/scan", headers=admin_headers)

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "SCRIPT_ROOT_NOT_FOUND"
    assert body["detail"]["message"]


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
