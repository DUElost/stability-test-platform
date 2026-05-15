import pytest


def test_get_cors_config_rejects_wildcard_origin(monkeypatch):
    from backend.core.cors import get_cors_config

    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,*")

    with pytest.raises(RuntimeError, match="must not contain wildcard"):
        get_cors_config()


def test_get_cors_config_rejects_wildcard_methods(monkeypatch):
    from backend.core.cors import get_cors_config

    monkeypatch.setenv("CORS_ALLOW_METHODS", "*")

    with pytest.raises(RuntimeError, match="CORS_ALLOW_METHODS"):
        get_cors_config()


def test_get_cors_config_rejects_wildcard_headers(monkeypatch):
    from backend.core.cors import get_cors_config

    monkeypatch.setenv("CORS_ALLOW_HEADERS", "Authorization,*")

    with pytest.raises(RuntimeError, match="CORS_ALLOW_HEADERS"):
        get_cors_config()
