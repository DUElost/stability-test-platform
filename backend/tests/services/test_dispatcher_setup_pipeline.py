"""Dispatcher pipeline composition tests."""

from backend.services.dispatcher import (
    _apply_step_overrides,
    _build_template_preview,
    _resolve_pipeline,
)


def _step(step_id: str) -> dict:
    return {
        "step_id": step_id,
        "action": "script:check_device",
        "version": "1.0.0",
        "timeout_seconds": 30,
    }


def test_resolve_pipeline_merges_workflow_setup_task_and_teardown():
    setup = {"lifecycle": {"init": [_step("setup_wifi")], "teardown": []}}
    task = {
        "lifecycle": {
            "init": [_step("task_prepare"), _step("task_execute")],
            "teardown": [_step("task_post")],
        },
    }
    teardown = {"lifecycle": {"init": [], "teardown": [_step("cleanup")]}}

    resolved = _resolve_pipeline(setup, task, teardown)

    assert resolved == {
        "lifecycle": {
            "init": [_step("setup_wifi"), _step("task_prepare"), _step("task_execute")],
            "teardown": [_step("task_post"), _step("cleanup")],
        }
    }


def test_resolve_pipeline_preserves_existing_task_pipeline_when_workflow_pipelines_are_null():
    task = {
        "lifecycle": {
            "init": [_step("task_prepare"), _step("task_execute")],
            "teardown": [_step("task_post")],
        },
    }

    assert _resolve_pipeline(None, task, None) == task


def test_apply_step_overrides_updates_matching_step_without_mutating_original():
    pipeline = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "run_monkey",
                    "action": "script:run_monkey",
                    "version": "1.0.0",
                    "params": {"duration": 300, "seed": 1},
                    "timeout_seconds": 400,
                    "retry": 0,
                },
                _step("other"),
            ],
            "teardown": [],
        }
    }

    resolved = _apply_step_overrides(
        pipeline,
        "monkey",
        [
            {
                "template_name": "monkey",
                "stage": "init",
                "step_id": "run_monkey",
                "params": {"duration": 600},
                "timeout_seconds": 700,
                "retry": 1,
                "enabled": False,
            },
            {
                "template_name": "other_template",
                "stage": "init",
                "step_id": "other",
                "enabled": False,
            },
        ],
    )

    updated = resolved["lifecycle"]["init"][0]
    assert updated["params"] == {"duration": 600, "seed": 1}
    assert updated["timeout_seconds"] == 700
    assert updated["retry"] == 1
    assert updated["enabled"] is False
    assert resolved["lifecycle"]["init"][1]["step_id"] == "other"
    assert "enabled" not in resolved["lifecycle"]["init"][1]
    assert "enabled" not in pipeline["lifecycle"]["init"][0]


def test_build_template_preview_counts_disabled_and_executable_steps():
    pipeline = {
        "lifecycle": {
            "init": [
                _step("prepare"),
                {**_step("enabled"), "enabled": True},
                {**_step("disabled"), "enabled": False},
            ],
            "teardown": [],
        }
    }

    preview = _build_template_preview("monkey", pipeline)

    assert preview["name"] == "monkey"
    assert preview["total_steps"] == 3
    assert preview["disabled_steps"] == 1
    assert preview["executable_steps"] == 2
    assert preview["resolved_pipeline"] == pipeline
