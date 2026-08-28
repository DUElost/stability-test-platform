# -*- coding: utf-8 -*-
"""AI 助手凭据加密（ADR-0031 D3）。

克隆 core/ssh_security.py 的 Fernet 模式，密钥域独立
（AI_ASSISTANT_FERNET_KEY，与 SSH_CREDENTIALS_FERNET_KEY 不混用）。
仅 TESTING=1 允许测试兜底键；未配置 = 功能降级（AiSecurityConfigError），
不做 lifespan fail-fast（不阻塞平台其余功能）。
"""

import os

from cryptography.fernet import Fernet, InvalidToken

AI_ASSISTANT_FERNET_ENV = "AI_ASSISTANT_FERNET_KEY"

# 32 字节全零的 urlsafe base64——确定性测试兜底键（仅 TESTING=1 使用）
_TEST_FERNET_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


class AiSecurityConfigError(RuntimeError):
    """AI 助手加密键未配置或密文不可解。"""


def _get_fernet() -> Fernet:
    key = os.getenv(AI_ASSISTANT_FERNET_ENV, "").strip()
    if not key:
        if os.getenv("TESTING") == "1":
            key = _TEST_FERNET_KEY
        else:
            raise AiSecurityConfigError(
                f"{AI_ASSISTANT_FERNET_ENV} not configured"
            )
    return Fernet(key.encode("utf-8"))


def encrypt_api_key(api_key: str) -> str:
    secret = (api_key or "").strip()
    if not secret:
        return ""
    return _get_fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def decrypt_api_key(ciphertext: str) -> str:
    token = (ciphertext or "").strip()
    if not token:
        return ""
    try:
        return _get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise AiSecurityConfigError("encrypted AI api key cannot be decrypted") from exc


def mask_api_key(api_key: str) -> str:
    """掩码展示：保留末 4 位（配置 API 永不回明文）。"""
    secret = (api_key or "").strip()
    if not secret:
        return ""
    return f"{'*' * max(len(secret) - 4, 3)}{secret[-4:]}"
