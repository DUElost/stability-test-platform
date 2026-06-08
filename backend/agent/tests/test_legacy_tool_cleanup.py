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


def test_legacy_pipeline_cleanup_sql_helpers_are_removed():
    legacy_sql = [
        REPO_ROOT / "tools" / "sql" / "cleanup_legacy_pipeline_data.sql",
        REPO_ROOT / "tools" / "sql" / "scan_legacy_action_prefix.sql",
    ]

    for path in legacy_sql:
        assert not path.exists(), f"legacy one-off SQL cleanup helper still present: {path}"


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
