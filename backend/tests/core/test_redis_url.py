"""Tests for Redis URL log redaction."""

from __future__ import annotations

from backend.core.redis import redact_redis_url


def test_redact_redis_url_strips_password():
    assert (
        redact_redis_url("redis://user:s3cret@redis.example:6379/0")
        == "redis://user:***@redis.example:6379/0"
    )


def test_redact_redis_url_keeps_url_without_password():
    assert redact_redis_url("redis://redis.example:6379/0") == "redis://redis.example:6379/0"
