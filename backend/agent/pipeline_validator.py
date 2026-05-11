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


def _iter_lifecycle_steps(pipeline_def: Dict[str, Any]):
    lifecycle = pipeline_def.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return

    for phase_name in ("init", "teardown"):
        steps = lifecycle.get(phase_name)
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                yield f"lifecycle.{phase_name}.{index}", step

    patrol = lifecycle.get("patrol")
    if isinstance(patrol, dict):
        steps = patrol.get("steps")
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                yield f"lifecycle.patrol.steps.{index}", step


def _validate_action_versions(pipeline_def: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for path, step in _iter_lifecycle_steps(pipeline_def):
        if not isinstance(step, dict):
            continue
        action = step.get("action", "")
        if isinstance(action, str) and action.startswith("script:") and not step.get("version"):
            errors.append(f"{path}.version: version is required for script action")
    return errors


def validate_lifecycle_semantics(pipeline_def: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate lifecycle structure and action semantics (no JSON Schema dependency).

    This is the shared semantic validator used by both the backend API
    (before dispatch) and the agent (before execution).  It does NOT depend on
    jsonschema — that layer is added by ``validate_pipeline_def``.

    Returns (is_valid, errors_list).
    """
    if not isinstance(pipeline_def, dict):
        return False, ["(root): pipeline_def must be an object"]

    if "phases" in pipeline_def:
        return False, ["(root): legacy 'phases' format is not supported; use 'lifecycle'"]

    if "stages" in pipeline_def:
        return False, ["(root): stages format is not supported; use 'lifecycle'"]

    if "lifecycle" not in pipeline_def:
        return False, ["(root): pipeline must define 'lifecycle'"]

    lifecycle = pipeline_def.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return False, ["(root): lifecycle must be an object"]

    init = lifecycle.get("init")
    if not isinstance(init, list):
        return False, ["lifecycle.init: must be a step array"]
    if len(init) == 0:
        return False, ["lifecycle.init: at least one step is required"]

    teardown = lifecycle.get("teardown")
    if not isinstance(teardown, list):
        return False, ["lifecycle.teardown: must be a step array"]

    patrol = lifecycle.get("patrol")
    if patrol is not None:
        if not isinstance(patrol, dict):
            return False, ["lifecycle.patrol: must be an object"]
        interval = patrol.get("interval_seconds")
        if not isinstance(interval, int) or interval < 1:
            return False, ["lifecycle.patrol.interval_seconds: must be a positive integer"]
        steps = patrol.get("steps")
        if not isinstance(steps, list):
            return False, ["lifecycle.patrol.steps: must be a step array"]
        if len(steps) == 0:
            return False, ["lifecycle.patrol.steps: at least one step is required"]

    action_version_errors = _validate_action_versions(pipeline_def)
    if action_version_errors:
        return False, action_version_errors

    return True, []


def validate_pipeline_def(pipeline_def: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a pipeline definition — semantics + JSON Schema.

    Supports only lifecycle format:
    { "lifecycle": { "init": [...], "patrol": { "steps": [...] }, "teardown": [...] } }

    Returns:
        (is_valid, errors) where errors is a list of human-readable error strings.
    """
    if Draft7Validator is None:
        return False, ["jsonschema library not installed; pipeline validation cannot proceed. Install with: pip install jsonschema"]

    ok_sem, errors_sem = validate_lifecycle_semantics(pipeline_def)
    if not ok_sem:
        return False, errors_sem

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
