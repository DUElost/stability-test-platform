from __future__ import annotations

from backend.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)


def test_access_token_roundtrip():
    token = create_access_token({"sub": "alice", "role": "admin"})

    payload = decode_token(token)

    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["role"] == "admin"
    assert payload["type"] == "access"


def test_refresh_token_roundtrip():
    token = create_refresh_token({"sub": "alice"})

    payload = decode_token(token)

    assert payload is not None
    assert payload["sub"] == "alice"
    assert payload["type"] == "refresh"
