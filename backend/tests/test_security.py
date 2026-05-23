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


# ── ADR-0024 P0: decode_token expected_type ────────────────────────────────


def test_decode_token_without_expected_type_returns_payload_for_any_type():
    """向后兼容:不传 expected_type 时,任何 type 的 token 都能解出 payload。"""
    access = create_access_token({"sub": "alice", "role": "admin"})
    refresh = create_refresh_token({"sub": "alice"})

    assert decode_token(access)["type"] == "access"
    assert decode_token(refresh)["type"] == "refresh"


def test_decode_token_with_matching_expected_type_returns_payload():
    access = create_access_token({"sub": "alice", "role": "admin"})
    refresh = create_refresh_token({"sub": "alice"})

    assert decode_token(access, expected_type="access")["sub"] == "alice"
    assert decode_token(refresh, expected_type="refresh")["sub"] == "alice"


def test_decode_token_rejects_refresh_when_access_expected():
    """ADR-0024 P0: refresh token 不可冒充 access。"""
    refresh = create_refresh_token({"sub": "alice"})
    assert decode_token(refresh, expected_type="access") is None


def test_decode_token_rejects_access_when_refresh_expected():
    """ADR-0024 P0: access token 不可冒充 refresh。"""
    access = create_access_token({"sub": "alice", "role": "admin"})
    assert decode_token(access, expected_type="refresh") is None


def test_decode_token_returns_none_for_invalid_token_regardless_of_expected_type():
    assert decode_token("garbage.token.value", expected_type="access") is None
    assert decode_token("garbage.token.value", expected_type="refresh") is None
