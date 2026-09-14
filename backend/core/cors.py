"""Shared CORS configuration and validation.

ADR-0042 P2（安全与会话域）：白名单三个键收敛到
:class:`backend.core.settings.security.AuthSessionSettings`（单一来源 + 默认值随迁）；
本模块只保留**解析与护栏**（CSV 拆分、通配符拒绝、凭据模式约束）。
"""

from __future__ import annotations

from backend.core.settings.security import get_auth_session_settings


def get_cors_config() -> dict[str, object]:
    settings = get_auth_session_settings()
    origins = _parse_csv(settings.cors_origins)
    methods = _parse_csv(settings.cors_allow_methods)
    headers = _parse_csv(settings.cors_allow_headers)

    if not origins:
        raise RuntimeError("CORS_ORIGINS must contain at least one explicit origin")
    if any("*" in origin for origin in origins):
        raise RuntimeError(
            "CORS_ORIGINS must not contain wildcard when credentials are enabled"
        )
    if any(method == "*" for method in methods):
        raise RuntimeError("CORS_ALLOW_METHODS must not contain wildcard")
    if any(header == "*" for header in headers):
        raise RuntimeError("CORS_ALLOW_HEADERS must not contain wildcard")

    return {
        "allow_origins": origins,
        "allow_credentials": True,
        "allow_methods": methods,
        "allow_headers": headers,
    }


def get_cors_allowed_origins() -> list[str]:
    return list(get_cors_config()["allow_origins"])


def _parse_csv(raw: str) -> list[str]:
    """CSV → 去空白、丢空项（迁移前 ``_parse_csv_env`` 的逐字语义）。"""
    return [item.strip() for item in raw.split(",") if item.strip()]
