from backend.core.pipeline_validator import validate_pipeline_def


def test_validate_pipeline_def_accepts_stages_format():
    pipeline_def = {
        "stages": {
            "prepare": [
                {
                    "step_id": "check_device",
                    "action": "builtin:check_device",
                    "params": {},
                    "timeout_seconds": 30,
                    "retry": 0,
                }
            ]
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is True
    assert errors == []


def test_validate_pipeline_def_rejects_legacy_phases_format():
    pipeline_def = {
        "phases": [
            {
                "name": "prepare",
                "steps": [
                    {
                        "name": "check_device",
                        "action": "builtin:check_device",
                        "timeout": 30,
                    }
                ],
            }
        ]
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("legacy 'phases'" in err for err in errors)


def test_validate_pipeline_def_rejects_empty_stages():
    pipeline_def = {
        "stages": {
            "prepare": [],
            "execute": [],
            "post_process": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("at least one step" in err for err in errors)
