"""#977：脚本参数有效值语义（merge_effective_params / validate_params_against_schema）。"""
from __future__ import annotations

from backend.services.script_params import (
    merge_effective_params,
    validate_params_against_schema,
)

SCHEMA = {
    "timeout": {"type": "integer", "default": 30},
    "mode": {"type": "string", "enum": ["fast", "full"], "default": "fast"},
    "flag": {"type": "boolean", "default": False},
}


class TestMergeEffectiveParams:
    def test_priority_matches_frontend(self):
        """step.params > default_params > schema.default（与前端展示同源）。"""
        merged = merge_effective_params(
            SCHEMA,
            {"timeout": 60, "extra": "d"},
            {"mode": "full"},
        )
        assert merged == {"timeout": 60, "mode": "full", "flag": False, "extra": "d"}

    def test_schema_default_only(self):
        assert merge_effective_params(SCHEMA, None, None) == {
            "timeout": 30, "mode": "fast", "flag": False,
        }

    def test_missing_schema_and_defaults_is_empty(self):
        assert merge_effective_params(None, None, None) == {}

    def test_no_mutation_of_inputs(self):
        defaults = {"timeout": 60}
        step = {"mode": "full"}
        merge_effective_params(SCHEMA, defaults, step)
        assert defaults == {"timeout": 60}
        assert step == {"mode": "full"}

    def test_field_without_default_is_skipped(self):
        schema = {"required_only": {"type": "string", "required": True}}
        assert merge_effective_params(schema, None, None) == {}


class TestValidateParamsAgainstSchema:
    def test_valid_values_pass(self):
        assert validate_params_against_schema(
            {"timeout": 10, "mode": "fast", "flag": True}, SCHEMA,
        ) == []

    def test_wrong_type_rejected(self):
        problems = validate_params_against_schema({"timeout": "10"}, SCHEMA)
        assert len(problems) == 1
        assert "timeout" in problems[0] and "integer" in problems[0]

    def test_bool_is_not_integer(self):
        """Python 里 bool 是 int 子类——schema integer 不应接受 True。"""
        problems = validate_params_against_schema({"timeout": True}, SCHEMA)
        assert len(problems) == 1

    def test_enum_violation_rejected(self):
        problems = validate_params_against_schema({"mode": "turbo"}, SCHEMA)
        assert len(problems) == 1
        assert "one of" in problems[0]

    def test_undeclared_keys_are_free_mode(self):
        assert validate_params_against_schema({"custom": object()}, SCHEMA) == []

    def test_empty_inputs_pass(self):
        assert validate_params_against_schema({}, SCHEMA) == []
        assert validate_params_against_schema({"x": 1}, None) == []
