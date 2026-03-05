from backend.api.routes.pipeline import TEMPLATES_DIR, _load_template
from backend.core.pipeline_validator import validate_pipeline_def


def test_builtin_pipeline_templates_are_stages_only_and_valid():
    template_files = sorted(TEMPLATES_DIR.glob("*.json"))
    assert template_files, "no builtin pipeline templates found"

    for path in template_files:
        template = _load_template(path)
        pipeline_def = template.pipeline_def

        assert "phases" not in pipeline_def, f"{path.name} should not contain legacy phases"
        is_valid, errors = validate_pipeline_def(pipeline_def)
        assert is_valid, f"{path.name} invalid pipeline_def: {errors}"
