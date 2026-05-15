"""Shared CORS configuration and validation."""

from __future__ import annotations

import os

DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
)
DEFAULT_CORS_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
DEFAULT_CORS_HEADERS = ("Authorization", "Content-Type", "X-Agent-Secret")


def get_cors_config() -> dict[str, object]:
    origins = _parse_csv_env("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    methods = _parse_csv_env("CORS_ALLOW_METHODS", ",".join(DEFAULT_CORS_METHODS))
    headers = _parse_csv_env("CORS_ALLOW_HEADERS", ",".join(DEFAULT_CORS_HEADERS))

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


def _parse_csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]
