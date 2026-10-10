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


def test_nested_sensitive_path_clears_enum_const_and_examples():
    """嵌套路径必须连 enum/const/examples 一起按路径清除。

    修复前：父字段只递归 default，enum/const/examples 原样深拷贝，哨兵仍在。
    修复后：四个槽位都按相对路径清除；非敏感兄弟值保留；输入对象不变。
    ``psk`` 不在敏感键名表里，只靠路径 ``("wifi","psk")``，用来区分「按键名碰巧掩掉」
    和「按参数路径掩掉」。
    """
    schema = {
        "wifi": {
            "type": "object",
            "label": "Wi-Fi",
            "default": {"ssid": "lab", "password": SENTINEL_A, "psk": SENTINEL_B},
            "enum": [{"ssid": "lab", "password": SENTINEL_A, "psk": SENTINEL_B}],
            "const": {"ssid": "lab", "password": SENTINEL_A, "psk": SENTINEL_B},
            "examples": [{"ssid": "lab", "password": SENTINEL_B, "psk": SENTINEL_A}],
            "properties": {
                "ssid": {
                    "type": "string",
                    "default": "lab",
                    "enum": ["lab"],
                    "const": "lab",
                    "examples": ["lab"],
                },
                "password": {
                    "type": "string",
                    "label": "密码",
                    "default": SENTINEL_A,
                    "enum": [SENTINEL_A],
                    "const": SENTINEL_A,
                    "examples": [SENTINEL_B],
                },
                "psk": {
                    "type": "string",
                    "default": SENTINEL_B,
                    "enum": [SENTINEL_B],
                    "const": SENTINEL_B,
                    "examples": [SENTINEL_A],
                },
            },
        }
    }
    redacted = redact_schema(schema, {("wifi", "password"), ("wifi", "psk")})
    dumped = json.dumps(redacted, ensure_ascii=False)
    assert SENTINEL_A not in dumped and SENTINEL_B not in dumped

    wifi = redacted["wifi"]
    assert wifi["label"] == "Wi-Fi"
    assert wifi["default"] == {"ssid": "lab", "password": None, "psk": None}
    assert wifi["enum"] == [{"ssid": "lab", "password": None, "psk": None}]
    assert wifi["const"] == {"ssid": "lab", "password": None, "psk": None}
    assert wifi["examples"] == [{"ssid": "lab", "password": None, "psk": None}]

    password = wifi["properties"]["password"]
    assert password["default"] is None
    assert password["enum"] is None
    assert password["const"] is None
    assert password["examples"] is None
    assert password["label"] == "密码"
    psk = wifi["properties"]["psk"]
    assert psk["default"] is None
    assert psk["enum"] is None
    assert psk["const"] is None
    assert psk["examples"] is None
    ssid = wifi["properties"]["ssid"]
    assert ssid["default"] == "lab"
    assert ssid["enum"] == ["lab"]
    assert ssid["const"] == "lab"
    assert ssid["examples"] == ["lab"]

    assert schema["wifi"]["enum"][0]["password"] == SENTINEL_A
    assert schema["wifi"]["const"]["psk"] == SENTINEL_B
    assert schema["wifi"]["examples"][0]["password"] == SENTINEL_B
    assert schema["wifi"]["properties"]["password"]["enum"] == [SENTINEL_A]
