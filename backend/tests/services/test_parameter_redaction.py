"""结构化掩码：敏感位置只留已设置/未设置，不暴露长度。"""
from __future__ import annotations

import json

from backend.services.parameter_redaction import (
    builtin_sensitive_paths,
    redact_params,
    redact_schema,
    union_sensitive_paths,
)

SENTINEL_A = "SENTINEL_PASSWORD_SHORT"
SENTINEL_B = "SENTINEL_PASSWORD_MUCH_LONGER_VALUE"


def test_password_lengths_collapse_to_the_same_null_shape():
    short = redact_params({"password": SENTINEL_A, "ssid": "lab"})
    long = redact_params({"password": SENTINEL_B, "ssid": "lab"})
    assert short == long == {"password": None, "ssid": "lab"}
    dumped = json.dumps(short, ensure_ascii=False)
    assert SENTINEL_A not in dumped and SENTINEL_B not in dumped


def test_nested_wifi_password_and_literal_dotted_key():
    assert builtin_sensitive_paths("connect_wifi") == {("password",)}
    assert builtin_sensitive_paths("monkey_setup") == {("wifi", "password")}
    nested = redact_params(
        {"wifi": {"ssid": "lab", "password": SENTINEL_A}},
        builtin_sensitive_paths("monkey_setup"),
    )
    assert nested["wifi"]["password"] is None
    assert nested["wifi"]["ssid"] == "lab"
    literal = redact_params(
        {"wifi.password": SENTINEL_A},
        builtin_sensitive_paths("monkey_setup"),
    )
    assert literal["wifi.password"] == SENTINEL_A
    masked_literal = redact_params(
        {"wifi.password": SENTINEL_A},
        union_sensitive_paths("monkey_setup", {("wifi.password",)}),
    )
    assert masked_literal["wifi.password"] is None


def test_case_and_nested_list_and_schema_slots():
    raw = {
        "Password": SENTINEL_A,
        "items": [{"token": SENTINEL_B, "name": "ok"}],
    }
    cleaned = redact_params(raw)
    assert cleaned["Password"] is None
    assert cleaned["items"][0]["token"] is None
    assert cleaned["items"][0]["name"] == "ok"
    assert raw["Password"] == SENTINEL_A

    schema = {
        "password": {
            "type": "string",
            "default": SENTINEL_A,
            "enum": [SENTINEL_A],
            "const": SENTINEL_A,
            "examples": [SENTINEL_B],
            "label": "密码",
        }
    }
    redacted = redact_schema(schema, {("password",)})
    assert redacted["password"]["default"] is None
    assert redacted["password"]["enum"] is None
    assert redacted["password"]["const"] is None
    assert redacted["password"]["examples"] is None
    assert redacted["password"]["label"] == "密码"
    assert schema["password"]["default"] == SENTINEL_A
