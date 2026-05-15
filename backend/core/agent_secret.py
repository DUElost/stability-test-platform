"""Shared AGENT_SECRET validation helpers."""

from __future__ import annotations

import os

AGENT_SECRET_PLACEHOLDER = "change-me-in-production"


class AgentSecretNotConfiguredError(RuntimeError):
    """Raised when AGENT_SECRET is required but missing or unsafe."""


def is_testing() -> bool:
    return os.getenv("TESTING") == "1"


def get_agent_secret() -> str:
    return os.getenv("AGENT_SECRET", "").strip()


def is_agent_secret_configured(secret: str | None = None) -> bool:
    candidate = get_agent_secret() if secret is None else secret.strip()
    return bool(candidate) and candidate != AGENT_SECRET_PLACEHOLDER


def require_agent_secret() -> str:
    secret = get_agent_secret()
    if is_testing():
        return secret
    if not is_agent_secret_configured(secret):
        raise AgentSecretNotConfiguredError("AGENT_SECRET not configured")
    return secret
