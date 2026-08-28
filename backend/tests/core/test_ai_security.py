"""AI 助手凭据加密单测（无 PG）。"""

import pytest
from cryptography.fernet import Fernet

from backend.core.ai_security import (
    AiSecurityConfigError,
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)


def test_roundtrip(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    token = encrypt_api_key("sk-secret-1234")
    assert token != "sk-secret-1234"
    assert decrypt_api_key(token) == "sk-secret-1234"


def test_empty_values(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    assert encrypt_api_key("") == ""
    assert decrypt_api_key("") == ""


def test_wrong_key_raises(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    token = encrypt_api_key("sk-secret-1234")
    # 换一把有效但不同的键 → 解不开
    monkeypatch.setenv("AI_ASSISTANT_FERNET_KEY", Fernet.generate_key().decode())
    with pytest.raises(AiSecurityConfigError):
        decrypt_api_key(token)


def test_missing_key_without_testing(monkeypatch):
    monkeypatch.delenv("AI_ASSISTANT_FERNET_KEY", raising=False)
    monkeypatch.delenv("TESTING", raising=False)
    with pytest.raises(AiSecurityConfigError):
        encrypt_api_key("sk-x")


def test_mask_keeps_last4():
    masked = mask_api_key("sk-abcdefgh")
    assert masked.endswith("efgh")
    assert "abcdefgh" not in masked
    assert set(masked[:-4]) == {"*"}
    assert mask_api_key("") == ""
