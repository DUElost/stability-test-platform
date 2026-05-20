from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.csrf import CSRFOriginMiddleware

ALLOWED = ("http://localhost:5173", "http://127.0.0.1:5173")


def _build_app(enabled: bool = True) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        CSRFOriginMiddleware,
        allowed_origins=ALLOWED,
        enabled=enabled,
    )

    @app.get("/api/v1/ping")
    def ping_get():
        return {"ok": True}

    @app.post("/api/v1/ping")
    def ping_post():
        return {"ok": True}

    @app.put("/api/v1/ping/{n}")
    def ping_put(n: int):
        return {"ok": True, "n": n}

    @app.delete("/api/v1/ping/{n}")
    def ping_delete(n: int):
        return {"ok": True}

    @app.post("/health/probe")
    def probe():
        return {"ok": True}

    return TestClient(app)


def test_safe_method_passes_without_origin():
    client = _build_app()
    r = client.get("/api/v1/ping")
    assert r.status_code == 200


def test_unsafe_method_with_allowed_origin_passes():
    client = _build_app()
    r = client.post("/api/v1/ping", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200


def test_unsafe_method_with_disallowed_origin_blocked():
    client = _build_app()
    r = client.post("/api/v1/ping", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert r.json()["detail"] == "CSRF check failed"


def test_referer_fallback_matches_allowed_origin():
    client = _build_app()
    r = client.put(
        "/api/v1/ping/7",
        headers={"Referer": "http://127.0.0.1:5173/plans/edit/3"},
    )
    assert r.status_code == 200


def test_referer_with_foreign_origin_blocked():
    client = _build_app()
    r = client.delete(
        "/api/v1/ping/7",
        headers={"Referer": "https://attacker.example.com/x"},
    )
    assert r.status_code == 403


def test_missing_origin_and_referer_blocked():
    client = _build_app()
    r = client.post("/api/v1/ping")
    assert r.status_code == 403


def test_bearer_authorization_bypasses_origin_check():
    client = _build_app()
    r = client.post(
        "/api/v1/ping",
        headers={
            "Origin": "https://anything-goes.example.com",
            "Authorization": "Bearer abc.def.ghi",
        },
    )
    assert r.status_code == 200


def test_agent_secret_header_bypasses_origin_check():
    client = _build_app()
    r = client.post(
        "/api/v1/ping",
        headers={
            "Origin": "https://anything-goes.example.com",
            "X-Agent-Secret": "shared-secret",
        },
    )
    assert r.status_code == 200


def test_null_origin_is_rejected():
    client = _build_app()
    r = client.post("/api/v1/ping", headers={"Origin": "null"})
    assert r.status_code == 403


def test_non_api_prefix_path_is_not_protected():
    client = _build_app()
    r = client.post("/health/probe", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200


def test_disabled_middleware_bypasses_all_checks():
    client = _build_app(enabled=False)
    r = client.post("/api/v1/ping", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200


def test_origin_match_is_strict_no_subdomain_relaxation():
    client = _build_app()
    r = client.post(
        "/api/v1/ping",
        headers={"Origin": "http://api.localhost:5173"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("method", ["HEAD", "OPTIONS"])
def test_other_safe_methods_pass(method):
    client = _build_app()
    r = client.request(method, "/api/v1/ping")
    assert r.status_code in (200, 405)
