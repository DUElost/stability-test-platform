"""Secret redaction 单测（无 PG）。R13-F02 (#1214)."""

from backend.core.redaction import redact_secrets


def test_url_query_token_redacted():
    text = "failed for https://hooks.example.com/notify?access_token=ABC123&channel=x"
    out = redact_secrets(text)
    assert "ABC123" not in out
    assert "access_token=***" in out
    assert "channel=x" in out  # 非敏感参数保留
    assert "hooks.example.com" in out


def test_url_userinfo_redacted():
    out = redact_secrets("https://user:pw@example.com/path")
    assert "user" not in out.split("://", 1)[1].split("@", 1)[0]
    assert "example.com" in out


def test_multiple_sensitive_keys_and_sign():
    out = redact_secrets(
        "https://oapi.dingtalk.com/robot/send?access_token=t&timestamp=1&sign=sig"
    )
    assert "access_token=***" in out and "sign=***" in out
    assert "timestamp=1" in out


def test_authorization_header_redacted():
    out = redact_secrets("Authorization: Bearer sk-verysecretvalue")
    assert "sk-verysecretvalue" not in out
    assert "Bearer ***" in out


def test_non_url_text_untouched():
    assert redact_secrets("plain message with no secrets") == "plain message with no secrets"


def test_empty_text():
    assert redact_secrets("") == ""
