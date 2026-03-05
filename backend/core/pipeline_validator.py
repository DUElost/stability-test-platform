"""Pipeline definition validator using JSON Schema."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from jsonschema import Draft7Validator, ValidationError
except ImportError:
    Draft7Validator = None
    ValidationError = None

_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "pipeline_schema.json"
_schema_cache: Optional[dict] = None


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            _schema_cache = json.load(f)
    return _schema_cache


def validate_pipeline_def(pipeline_def: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a pipeline definition against the JSON Schema.

    Returns:
        (is_valid, errors) where errors is a list of human-readable error strings.
    """
    if Draft7Validator is None:
        # jsonschema not installed — this is a deployment error, not a validation pass
        return False, ["jsonschema library not installed; pipeline validation cannot proceed. Install with: pip install jsonschema"]

    if not isinstance(pipeline_def, dict):
        return False, ["(root): pipeline_def must be an object"]

    if "phases" in pipeline_def:
        return False, ["(root): legacy 'phases' format is not supported; use 'stages'"]

    stages = pipeline_def.get("stages")
    if not isinstance(stages, dict):
        return False, ["(root): pipeline must define object field 'stages'"]

    has_any_step = any(
        isinstance(stages.get(stage_name), list) and len(stages.get(stage_name) or []) > 0
        for stage_name in ("prepare", "execute", "post_process")
    )
    if not has_any_step:
        return False, ["stages: at least one step is required across prepare/execute/post_process"]

    schema = _load_schema()
    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(pipeline_def), key=lambda e: list(e.path))

    if not errors:
        return True, []

    messages = []
    for err in errors:
        path = ".".join(str(p) for p in err.absolute_path) or "(root)"
        messages.append(f"{path}: {err.message}")
    return False, messages
