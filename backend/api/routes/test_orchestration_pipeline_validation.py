import pytest
from fastapi import HTTPException

from backend.api.routes.orchestration import TaskTemplateIn, _validate_task_templates


def test_validate_task_templates_accepts_lifecycle_script_steps():
    templates = [
        TaskTemplateIn(
            name="default",
            pipeline_def={
                "lifecycle": {
                    "init": [
                        {
                            "step_id": "check_device",
                            "action": "script:check_device",
                            "version": "1.0.0",
                            "timeout_seconds": 30,
                            "params": {},
                        }
                    ],
                    "teardown": [],
                }
            },
            sort_order=0,
        )
    ]

    _validate_task_templates(templates)


def test_validate_task_templates_rejects_stages():
    templates = [
        TaskTemplateIn(
            name="stages",
            pipeline_def={
                "stages": {
                    "execute": [
                        {
                            "step_id": "check_device",
                            "action": "script:check_device",
                            "version": "1.0.0",
                            "timeout_seconds": 30,
                            "params": {},
                        }
                    ]
                }
            },
            sort_order=0,
        )
    ]

    with pytest.raises(HTTPException) as exc_info:
        _validate_task_templates(templates)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["template_name"] == "stages"


def test_validate_task_templates_rejects_legacy_phases():
    templates = [
        TaskTemplateIn(
            name="legacy",
            pipeline_def={
                "phases": [
                    {
                        "name": "prepare",
                        "steps": [
                            {
                                "name": "check_device",
                                "action": "builtin:check_device",
                            }
                        ],
                    }
                ]
            },
            sort_order=0,
        )
    ]

    with pytest.raises(HTTPException) as exc_info:
        _validate_task_templates(templates)

    assert exc_info.value.status_code == 422
    detail = exc_info.value.detail
    assert detail["code"] == "INVALID_PIPELINE_DEF"
    assert detail["template_name"] == "legacy"
