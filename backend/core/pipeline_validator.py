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


def _has_any_step(stages: dict) -> bool:
    """Check that at least one step exists across prepare/execute/post_process."""
    return any(
        isinstance(stages.get(stage_name), list) and len(stages.get(stage_name) or []) > 0
        for stage_name in ("prepare", "execute", "post_process")
    )


def validate_pipeline_def(pipeline_def: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a pipeline definition against the JSON Schema.

    Supports two formats:
    - stages format: { "stages": { "prepare": [...], "execute": [...], "post_process": [...] } }
    - lifecycle format: { "lifecycle": { "init": {...}, "patrol": {...}, "teardown": {...} } }

    Returns:
        (is_valid, errors) where errors is a list of human-readable error strings.
    """
    if Draft7Validator is None:
        return False, ["jsonschema library not installed; pipeline validation cannot proceed. Install with: pip install jsonschema"]

    if not isinstance(pipeline_def, dict):
        return False, ["(root): pipeline_def must be an object"]

    if "phases" in pipeline_def:
        return False, ["(root): legacy 'phases' format is not supported; use 'stages'"]

    is_lifecycle = "lifecycle" in pipeline_def
    is_stages = "stages" in pipeline_def

    if not is_lifecycle and not is_stages:
        return False, ["(root): pipeline must define either 'stages' or 'lifecycle'"]

    # Semantic validation before JSON Schema
    if is_stages and not is_lifecycle:
        stages = pipeline_def.get("stages")
        if not isinstance(stages, dict):
            return False, ["(root): pipeline must define object field 'stages'"]
        if not _has_any_step(stages):
            return False, ["stages: at least one step is required across prepare/execute/post_process"]

    if is_lifecycle:
        lifecycle = pipeline_def.get("lifecycle")
        if not isinstance(lifecycle, dict):
            return False, ["(root): lifecycle must be an object"]
        for phase_name in ("init", "teardown"):
            phase = lifecycle.get(phase_name)
            if not isinstance(phase, dict) or "stages" not in phase:
                return False, [f"lifecycle.{phase_name}: must define 'stages'"]
            if not _has_any_step(phase.get("stages", {})):
                return False, [f"lifecycle.{phase_name}.stages: at least one step is required"]
        patrol = lifecycle.get("patrol")
        if patrol is not None:
            if not isinstance(patrol, dict) or "stages" not in patrol:
                return False, ["lifecycle.patrol: must define 'stages'"]
            interval = patrol.get("interval_seconds")
            if not isinstance(interval, int) or interval < 1:
                return False, ["lifecycle.patrol.interval_seconds: must be a positive integer"]

    # JSON Schema validation
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
