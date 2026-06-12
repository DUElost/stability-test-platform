import json

import pytest
from fastapi import HTTPException

import backend.api.routes.pipeline as pipeline_routes
from backend.api.routes.pipeline import TEMPLATES_DIR, _load_template
from backend.core.pipeline_validator import validate_pipeline_def


def test_builtin_pipeline_templates_do_not_use_legacy_phases():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def

        assert "phases" not in pipeline_def, f"{path.name} should not contain legacy phases"


def test_builtin_pipeline_templates_follow_current_validator():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def
        is_valid, errors = validate_pipeline_def(pipeline_def)
        assert is_valid, f"{path.name} should be valid: {errors}"
        assert "lifecycle" in pipeline_def


def test_monkey_watcher_patrol_template_is_watcher_only_public_variant():
    data = json.loads((TEMPLATES_DIR / "monkey_watcher_patrol.json").read_text(encoding="utf-8"))
    patrol_steps = data["lifecycle"]["patrol"]["steps"]
    step_ids = [str(step.get("step_id") or "") for step in patrol_steps]
    actions = [str(step.get("action") or "") for step in patrol_steps]

    assert data["version"] == 2
    assert "AEE" not in data["description"]
    assert step_ids == ["monkey_check"]
    assert "script:scan_aee" not in actions
    assert "script:export_mobilelogs" not in actions


def test_legacy_aee_template_alias_files_removed_from_repo():
    removed = (
        "aimonkey.json",
        "aimonkey_launcher_lifecycle.json",
        "monkey_aee.json",
        "monkey_aee_patrol.json",
        "monkey_aee_init.json",
        "monkey_aee_lifecycle.json",
        "monkey_aee_teardown.json",
    )
    for name in removed:
        assert not (TEMPLATES_DIR / name).exists(), f"{name} should be removed after watcher 收口"


def test_list_pipeline_templates_filters_legacy_alias_files_even_if_reintroduced(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(pipeline_routes, "TEMPLATES_DIR", tmp_path)

    (tmp_path / "monkey_watcher_patrol.json").write_text(
        json.dumps({"description": "Watcher", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )
    (tmp_path / "monkey_aee_patrol.json").write_text(
        json.dumps({"description": "Legacy", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )

    templates = pipeline_routes.list_pipeline_templates()

    assert [template.name for template in templates] == ["monkey_watcher_patrol"]


def test_get_pipeline_template_rejects_legacy_alias_even_if_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_routes, "TEMPLATES_DIR", tmp_path)
    (tmp_path / "monkey_aee_patrol.json").write_text(
        json.dumps({"description": "Legacy", "lifecycle": {"init": []}}),
        encoding="utf-8",
    )

    with pytest.raises(HTTPException) as excinfo:
        pipeline_routes.get_pipeline_template("monkey_aee_patrol")

    assert excinfo.value.status_code == 404
