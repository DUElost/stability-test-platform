"""Regression checks for the script-only agent cleanup."""

import importlib.util
import sys
from pathlib import Path

import backend.agent.pipeline_engine as pipeline_engine


REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_monkey_launch(version: str):
    script_dir = REPO_ROOT / "backend" / "agent" / "scripts" / "monkey_launch" / version
    module_path = script_dir / "monkey_launch.py"
    sys.path.insert(0, str(script_dir))
    try:
        spec = importlib.util.spec_from_file_location(f"_test_monkey_launch_{version}", module_path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(script_dir))


def test_pipeline_engine_no_longer_exposes_pipeline_action_base():
    assert not hasattr(pipeline_engine, "PipelineAction")


def test_removed_tool_catalog_terms_do_not_reappear_in_agent_sources():
    paths = [
        REPO_ROOT / "backend" / "agent" / "pipeline_engine.py",
        REPO_ROOT / "backend" / "agent" / "install_agent.sh",
        REPO_ROOT / "backend" / "agent" / "DEPLOY.md",
        REPO_ROOT / "backend" / "agent" / "scripts" / "monkey_launch" / "v1.0.0" / "monkey_launch.py",
        REPO_ROOT / "backend" / "agent" / "scripts" / "monkey_launch" / "v2.0.0" / "monkey_launch.py",
    ]
    forbidden = [
        "PipelineAction",
        "TOOL_CATEGORY",
        "TOOL_DESCRIPTION",
        "/agent/tools/",
        "test_framework.py",
        "test_stages.py",
        "EXTERNAL_TOOL_DIR",
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for term in forbidden:
            assert term not in text, f"{term!r} remains in {path}"


def test_legacy_aee_script_directories_are_removed_from_agent_repo():
    legacy_dirs = [
        REPO_ROOT / "backend" / "agent" / "scripts" / "scan_aee",
        REPO_ROOT / "backend" / "agent" / "scripts" / "export_mobilelogs",
    ]

    for path in legacy_dirs:
        assert not path.exists(), f"legacy watcher-pre-mainline script directory still present: {path}"


def test_watcher_only_one_off_plan_script_is_removed():
    path = REPO_ROOT / "backend" / "scripts" / "apply_watcher_only_plan2.py"
    assert not path.exists(), f"legacy watcher rollout helper still present: {path}"

    consolidate_plan = REPO_ROOT / "docs" / "plans" / "watcher-consolidate-aee-2026-05-27.md"
    text = consolidate_plan.read_text(encoding="utf-8")
    assert "apply_watcher_only_plan2.py" not in text


def test_legacy_pipeline_cleanup_sql_helpers_are_removed():
    legacy_sql = [
        REPO_ROOT / "tools" / "sql" / "cleanup_legacy_pipeline_data.sql",
        REPO_ROOT / "tools" / "sql" / "cleanup_pytest_fixture_residue.sql",
        REPO_ROOT / "tools" / "sql" / "cleanup_script_sequence_history.sql",
        REPO_ROOT / "tools" / "sql" / "fix_script_paths_after_flatten.sql",
        REPO_ROOT / "tools" / "sql" / "scan_legacy_action_prefix.sql",
    ]

    for path in legacy_sql:
        assert not path.exists(), f"legacy one-off SQL cleanup helper still present: {path}"


def test_obsolete_sqlite_postgres_dual_write_verifier_is_removed():
    path = REPO_ROOT / "tools" / "verify_dual_write.py"
    assert not path.exists(), f"obsolete sqlite/postgres dual-write verifier still present: {path}"


def test_legacy_postgres_bootstrap_optimize_sql_is_removed():
    path = REPO_ROOT / "deploy" / "postgres" / "init" / "01-optimize.sql"
    assert not path.exists(), f"legacy postgres bootstrap optimize sql still present: {path}"


def test_legacy_frontend_task_details_route_is_removed():
    legacy_paths = [
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "TaskDetails.tsx",
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "TaskDetails.test.tsx",
        REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "taskDetailsState.ts",
    ]

    for path in legacy_paths:
        assert not path.exists(), f"legacy frontend task details artifact still present: {path}"

    router_path = REPO_ROOT / "frontend" / "src" / "router" / "index.tsx"
    router_text = router_path.read_text(encoding="utf-8")
    assert "TaskDetails" not in router_text
    assert 'path="tasks"' not in router_text
    assert 'path=":taskId"' not in router_text


def test_dual_track_phase_c_schema_validator_is_removed():
    path = REPO_ROOT / "backend" / "tests" / "e2e" / "validate_phase_c_state.py"
    assert not path.exists(), f"legacy dual-track schema validator still present: {path}"


def test_run_report_page_no_longer_lives_under_legacy_tasks_directory():
    legacy_path = REPO_ROOT / "frontend" / "src" / "pages" / "tasks" / "RunReportPage.tsx"
    assert not legacy_path.exists(), f"run report page still lives under legacy tasks directory: {legacy_path}"

    current_path = REPO_ROOT / "frontend" / "src" / "pages" / "runs" / "RunReportPage.tsx"
    assert current_path.exists(), f"run report page should live under runs directory: {current_path}"

    router_path = REPO_ROOT / "frontend" / "src" / "router" / "index.tsx"
    router_text = router_path.read_text(encoding="utf-8")
    assert "../pages/runs/RunReportPage" in router_text
    assert "../pages/tasks/RunReportPage" not in router_text


def test_legacy_backend_task_schema_module_is_removed():
    legacy_path = REPO_ROOT / "backend" / "api" / "schemas" / "task.py"
    assert not legacy_path.exists(), f"legacy backend task schema module still present: {legacy_path}"

    import backend.api as backend_api
    from backend.api import schemas as api_schemas

    assert "TaskCreate" not in backend_api.__dict__
    assert "TaskDispatch" not in backend_api.__dict__
    assert "TaskCreate" not in api_schemas.__dict__
    assert "TaskDispatch" not in api_schemas.__dict__
    assert "TaskOut" in api_schemas.__dict__


def test_legacy_tasks_socket_refresh_path_is_removed():
    frontend_socket_events = (
        REPO_ROOT / "frontend" / "src" / "utils" / "socketEvents.ts"
    ).read_text(encoding="utf-8")
    frontend_dashboard_hook = (
        REPO_ROOT / "frontend" / "src" / "hooks" / "useRealtimeDashboard.ts"
    ).read_text(encoding="utf-8")
    backend_socket_server = (
        REPO_ROOT / "backend" / "realtime" / "socketio_server.py"
    ).read_text(encoding="utf-8")

    assert "TASK_UPDATE" not in frontend_socket_events
    assert "taskUpdate" not in frontend_socket_events
    assert "TASK_UPDATE" not in frontend_dashboard_hook
    assert "queryKey: ['tasks']" not in frontend_dashboard_hook
    assert "broadcast_task_update" not in backend_socket_server
    assert '"task_update"' not in backend_socket_server


def test_m1_dual_write_runbook_and_recon_script_are_removed():
    legacy_paths = [
        REPO_ROOT / "backend" / "scripts" / "aee_dual_write_recon.py",
        REPO_ROOT / "docs" / "plans" / "watcher-aee-m1-dual-write-runbook.md",
    ]

    for path in legacy_paths:
        assert not path.exists(), f"legacy watcher dual-write artifact still present: {path}"

    consolidate_plan = REPO_ROOT / "docs" / "plans" / "watcher-consolidate-aee-2026-05-27.md"
    text = consolidate_plan.read_text(encoding="utf-8")
    assert "watcher-aee-m1-dual-write-runbook.md" not in text
    assert "aee_dual_write_recon.py" not in text


def test_watcher_summary_api_tests_do_not_reference_legacy_patrol_scripts():
    path = REPO_ROOT / "backend" / "tests" / "api" / "test_plan_run_aggregation_endpoints.py"
    text = path.read_text(encoding="utf-8")

    assert "script_name\": \"scan_aee\"" not in text
    assert "script_name\": \"export_mobilelogs\"" not in text


def test_legacy_aee_script_names_use_single_shared_source():
    from backend.agent.registry import script_registry
    from backend.agent.aee import state_migration
    from backend.api.routes import plans, scripts
    from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
    from backend.services import script_catalog

    assert plans.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert scripts.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_catalog.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES
    assert script_registry.LEGACY_AEE_SCRIPT_NAMES is LEGACY_AEE_SCRIPT_NAMES

    assert "_LEGACY_AEE_SCRIPT_NAMES" not in plans.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in scripts.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_catalog.__dict__
    assert "_LEGACY_AEE_SCRIPT_NAMES" not in script_registry.__dict__
    assert not hasattr(state_migration, "migrate_legacy_aee_state_store")


def test_monkey_launch_resolves_aimonkey_from_env_resource_root(tmp_path, monkeypatch):
    resource_root = tmp_path / "resources" / "aimonkey"
    aimonkey_dir = resource_root / "AIMonkeyTest_20260317"
    aimonkey_dir.mkdir(parents=True)
    (aimonkey_dir / "MonkeyTest.py").write_text("# test fixture\n", encoding="utf-8")
    monkeypatch.setenv("AIMONKEY_RESOURCE_DIR", str(resource_root))

    module = _load_monkey_launch("v1.0.0")
    assert module._resolve_aimonkey_dir({}) == aimonkey_dir


def test_monkey_launch_resolves_aimonkey_from_install_resource_root(tmp_path, monkeypatch):
    install_root = tmp_path / "stability-test-agent"
    script_dir = install_root / "agent" / "scripts" / "monkey_launch" / "v1.0.0"
    script_path = script_dir / "monkey_launch.py"
    aimonkey_dir = install_root / "resources" / "aimonkey" / "AIMonkeyTest_20260317"
    aimonkey_dir.mkdir(parents=True)
    monkeypatch.delenv("AIMONKEY_RESOURCE_DIR", raising=False)

    module = _load_monkey_launch("v1.0.0")
    monkeypatch.setattr(module, "__file__", str(script_path))
    assert module._resolve_aimonkey_dir({}) == aimonkey_dir
