"""Pipeline definition validator using JSON Schema.

契约模块（ADR-0054 D1/D2）：控制面与 Agent 共用**同一实现**——Agent 侧经相对
导入（``from ..contracts.pipeline_validator import …``），控制面经
``backend.agent.contracts.pipeline_validator``。模块体只依赖标准库与逐条登记的
``jsonschema``（缺失时 ``validate_pipeline_def`` 返回明确错误），import 期无 I/O。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from jsonschema import Draft7Validator, ValidationError
except ImportError:
    Draft7Validator = None
    ValidationError = None

_SCHEMA_FILENAME = "pipeline_schema.json"
_schema_cache: Optional[dict] = None


def resolve_pipeline_schema_path() -> Path:
    """定位运行时工件 ``pipeline_schema.json``（ADR-0054 D6，显式处理两种布局）。

    本文件位于 ``<agent 包>/contracts/``，schema 恒在 **agent 包目录的父目录** 下：

    - 仓库布局：``<repo>/backend/agent/contracts/…`` → ``<repo>/backend/schemas/pipeline_schema.json``
    - 主机安装布局：``<INSTALL_DIR>/agent/contracts/…`` → ``<INSTALL_DIR>/schemas/pipeline_schema.json``

    以 agent 包目录（``parents[1]``，即 ``contracts/`` 的上一级）为锚点，而不是
    按 ``__file__`` 裸深度计数：``contracts/`` 内再增删目录层级都不改变解析结果，
    两种布局共用同一条公式。
    """
    agent_package_root = Path(__file__).resolve().parents[1]
    return agent_package_root.parent / "schemas" / _SCHEMA_FILENAME


def _load_schema() -> dict:
    global _schema_cache
    if _schema_cache is None:
        with open(resolve_pipeline_schema_path(), "r", encoding="utf-8") as f:
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
