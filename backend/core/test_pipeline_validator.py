from backend.core.pipeline_validator import validate_pipeline_def


def test_validate_pipeline_def_accepts_lifecycle_script_steps():
    pipeline_def = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "check_device",
                    "action": "script:check_device",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                    "retry": 0,
                }
            ],
            "patrol": {
                "interval_seconds": 60,
                "steps": [
                    {
                        "step_id": "watch_device",
                        "action": "script:watch_device",
                        "version": "1.0.0",
                        "params": {},
                        "timeout_seconds": 30,
                    }
                ],
            },
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is True
    assert errors == []


def test_validate_pipeline_def_rejects_top_level_stages_format():
    pipeline_def = {
        "stages": {
            "prepare": [
                {
                    "step_id": "check_device",
                    "action": "script:check_device",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                }
            ]
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("lifecycle" in err for err in errors)


def test_validate_pipeline_def_rejects_legacy_phases_format():
    pipeline_def = {
        "phases": [
            {
                "name": "prepare",
                "steps": [
                    {
                        "name": "check_device",
                        "action": "script:check_device",
                        "timeout": 30,
                    }
                ],
            }
        ]
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("legacy 'phases'" in err for err in errors)


def test_validate_pipeline_def_rejects_lifecycle_phase_with_nested_stages():
    pipeline_def = {
        "lifecycle": {
            "init": {
                "stages": {
                    "prepare": [
                        {
                            "step_id": "check_device",
                            "action": "script:check_device",
                            "version": "1.0.0",
                            "params": {},
                            "timeout_seconds": 30,
                        }
                    ]
                }
            },
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("lifecycle.init" in err for err in errors)


def test_validate_pipeline_def_accepts_script_action_with_version():
    pipeline_def = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "push_bundle",
                    "action": "script:push_bundle",
                    "version": "2.0.0",
                    "params": {},
                    "timeout_seconds": 600,
                }
            ],
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is True
    assert errors == []


def test_validate_pipeline_def_rejects_script_action_without_version():
    pipeline_def = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "push_bundle",
                    "action": "script:push_bundle",
                    "params": {},
                    "timeout_seconds": 600,
                }
            ],
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("version is required for script action" in err for err in errors)


def test_validate_pipeline_def_rejects_action_with_invalid_prefix():
    pipeline_def = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "bad_action",
                    "action": "tool:1",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                }
            ],
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is False
    assert any("action" in err.lower() for err in errors)


def test_validate_pipeline_def_accepts_disabled_step():
    pipeline_def = {
        "lifecycle": {
            "init": [
                {
                    "step_id": "skip_me",
                    "action": "script:check_device",
                    "version": "1.0.0",
                    "params": {},
                    "timeout_seconds": 30,
                    "retry": 0,
                    "enabled": False,
                }
            ],
            "teardown": [],
        }
    }

    is_valid, errors = validate_pipeline_def(pipeline_def)

    assert is_valid is True
    assert errors == []
