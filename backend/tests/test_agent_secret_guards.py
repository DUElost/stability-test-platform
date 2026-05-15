from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.routes.agent_api import _verify_agent
from backend.api.routes.auth import verify_agent_secret
from backend.main import fastapi_app, lifespan


def test_verify_agent_secret_rejects_missing_server_secret(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("AGENT_SECRET", raising=False)

    with pytest.raises(HTTPException) as excinfo:
        verify_agent_secret("anything")

    assert excinfo.value.status_code == 503
    assert "AGENT_SECRET not configured" in str(excinfo.value.detail)


def test_agent_api_verify_rejects_placeholder_server_secret(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.setenv("AGENT_SECRET", "change-me-in-production")

    with pytest.raises(HTTPException) as excinfo:
        _verify_agent("change-me-in-production")

    assert excinfo.value.status_code == 503
    assert "AGENT_SECRET not configured" in str(excinfo.value.detail)


@pytest.mark.asyncio
async def test_lifespan_requires_agent_secret_outside_testing(monkeypatch):
    monkeypatch.setenv("TESTING", "0")
    monkeypatch.delenv("AGENT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="AGENT_SECRET required"):
        async with lifespan(fastapi_app):
            pass
