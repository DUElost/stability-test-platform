"""#977 / ADR-0067 D2：脚本参数有效值语义（merge_effective_params / validate_params_against_schema）。"""
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

LOG_PREPARE_SCHEMA = {
    "mobilelog_size_mb": {
        "type": "integer",
        "default": 4096,
        "minimum": 256,
        "maximum": 20480,
    },
    "mtk_auto_start": {"type": "boolean", "default": True},
    "mtk_sublog_cmd": {
        "type": "string",
        "nullable": True,
        "pattern": "^[A-Za-z0-9_.-]{1,64}$",
    },
    "aee_mode": {
        "type": "string",
        "nullable": True,
        "pattern": "^[0-9]{1,2}$",
    },
    "ylog_size_mb": {
        "type": "integer",
        "default": 4096,
        "minimum": 64,
        "maximum": 10240,
    },
    "ylog_rotate": {"type": "boolean", "default": True},
    "ylog_required_streams": {
        "type": "array",
        "default": ["android", "kernel"],
        "minItems": 1,
        "items": {"type": "string", "enum": ["android", "kernel", "uboot", "lastlog"]},
        "required_items": ["android", "kernel"],
    },
}


class TestMergeEffectiveParams:
    def test_priority_matches_frontend(self):
        """step.params > project_preset > default_params > schema.default（与前端展示同源）。"""
        merged = merge_effective_params(
            SCHEMA,
            {"timeout": 60, "extra": "d"},
            {"mode": "full"},
            project_preset={"timeout": 90, "mode": "fast", "flag": True},
        )
        assert merged == {
            "timeout": 90,
            "mode": "full",
            "flag": True,
            "extra": "d",
        }

    def test_project_preset_skips_null_keys_while_step_override_keeps_explicit_null(self):
        """ADR-0067 D2.2 / D2.3：第 3 层 null 不抹除脚本默认，第 4 层显式 null 可覆盖预设。"""
        merged = merge_effective_params(
            LOG_PREPARE_SCHEMA,
            {"mobilelog_size_mb": 4096, "ylog_size_mb": 4096},
            {"aee_mode": None},
            project_preset={"mobilelog_size_mb": None, "aee_mode": "3", "ylog_size_mb": 2048},
        )
        assert merged["mobilelog_size_mb"] == 4096
        assert merged["ylog_size_mb"] == 2048
        assert merged["aee_mode"] is None

    def test_schema_default_only(self):
        assert merge_effective_params(SCHEMA, None, None) == {
            "timeout": 30, "mode": "fast", "flag": False,
        }

    def test_missing_schema_and_defaults_is_empty(self):
        assert merge_effective_params(None, None, None) == {}

    def test_no_mutation_of_inputs(self):
        defaults = {"timeout": 60}
        preset = {"ylog_required_streams": ["android", "kernel"]}
        step = {"mode": "full"}
        merged = merge_effective_params(SCHEMA, defaults, step, project_preset=preset)
        merged["ylog_required_streams"].append("uboot")
        assert defaults == {"timeout": 60}
        assert preset == {"ylog_required_streams": ["android", "kernel"]}
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

    def test_nullable_allowed_and_non_nullable_null_rejected(self):
        """ADR-0067 D2.2：aee_mode/mtk_sublog_cmd 允许显式 null；非空字段拒绝 null。"""
        assert validate_params_against_schema(
            {"aee_mode": None, "mtk_sublog_cmd": None}, LOG_PREPARE_SCHEMA,
        ) == []
        for key in ("mobilelog_size_mb", "mtk_auto_start", "ylog_size_mb", "ylog_rotate", "ylog_required_streams"):
            problems = validate_params_against_schema({key: None}, LOG_PREPARE_SCHEMA)
            assert len(problems) == 1, key

    def test_capacity_bounds_pattern_and_array_constraints(self):
        """ADR-0067 D2.6：容量上下限、正则与展锐必留子流校验。"""
        assert validate_params_against_schema(
            {
                "mobilelog_size_mb": 256,
                "ylog_size_mb": 10240,
                "aee_mode": "3",
                "mtk_sublog_cmd": "set_sublog_4_5_0",
                "ylog_required_streams": ["android", "kernel", "uboot"],
            },
            LOG_PREPARE_SCHEMA,
        ) == []
        assert validate_params_against_schema({"mobilelog_size_mb": 255}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"mobilelog_size_mb": 20481}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"ylog_size_mb": 63}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"ylog_size_mb": 10241}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"aee_mode": "abc"}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"mtk_sublog_cmd": "bad cmd!"}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"ylog_required_streams": ["sysinfo"]}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"ylog_required_streams": ["android"]}, LOG_PREPARE_SCHEMA)
        assert validate_params_against_schema({"ylog_required_streams": []}, LOG_PREPARE_SCHEMA)

    def test_undeclared_keys_are_free_mode(self):
        assert validate_params_against_schema({"custom": object()}, SCHEMA) == []

    def test_empty_inputs_pass(self):
        assert validate_params_against_schema({}, SCHEMA) == []
        assert validate_params_against_schema({"x": 1}, None) == []
