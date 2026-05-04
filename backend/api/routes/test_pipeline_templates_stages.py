from backend.api.routes.pipeline import TEMPLATES_DIR, _load_template
from backend.core.pipeline_validator import validate_pipeline_def


def test_builtin_pipeline_templates_do_not_use_legacy_phases():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def

        assert "phases" not in pipeline_def, f"{path.name} should not contain legacy phases"


def test_builtin_pipeline_templates_follow_current_validator_when_migrated():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def
        is_valid, errors = validate_pipeline_def(pipeline_def)
        if is_valid:
            assert "lifecycle" in pipeline_def
            continue
        assert errors, f"{path.name} invalid pipeline_def should report errors"
