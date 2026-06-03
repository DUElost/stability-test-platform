import json

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


def test_monkey_aee_templates_are_watcher_only_m2_variants():
    expected_names = ("monkey_aee_patrol.json", "monkey_aee_lifecycle.json")
    for name in expected_names:
        data = json.loads((TEMPLATES_DIR / name).read_text(encoding="utf-8"))
        patrol_steps = data["lifecycle"]["patrol"]["steps"]
        step_ids = [str(step.get("step_id") or "") for step in patrol_steps]
        actions = [str(step.get("action") or "") for step in patrol_steps]

        assert data["version"] == 2, f"{name} should version-bump for M2 watcher-only rollout"
        assert step_ids == ["monkey_check"], f"{name} patrol should only keep monkey_check"
        assert "script:scan_aee" not in actions, f"{name} must not keep legacy scan_aee"
        assert "script:export_mobilelogs" not in actions, (
            f"{name} must not keep legacy export_mobilelogs"
        )
