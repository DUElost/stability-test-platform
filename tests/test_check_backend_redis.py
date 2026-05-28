import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "check_backend_redis.py"


spec = importlib.util.spec_from_file_location("check_backend_redis", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)

check_redis_or_explain = module.check_redis_or_explain
should_skip_redis_preflight = module.should_skip_redis_preflight
suggest_ipv4_loopback_url = module.suggest_ipv4_loopback_url


def test_should_skip_redis_preflight_only_for_non_production():
    assert (
        should_skip_redis_preflight(
            {"STP_SKIP_INFRA_CHECK": "1", "ENV": "development"}
        )
        is True
    )
    assert (
        should_skip_redis_preflight(
            {"STP_SKIP_INFRA_CHECK": "1", "ENV": "production"}
        )
        is False
    )
    assert should_skip_redis_preflight({"ENV": "development"}) is False


def test_suggest_ipv4_loopback_url_only_for_localhost():
    assert (
        suggest_ipv4_loopback_url("redis://localhost:6379/0")
        == "redis://127.0.0.1:6379/0"
    )
    assert suggest_ipv4_loopback_url("redis://127.0.0.1:6379/0") is None
    assert suggest_ipv4_loopback_url("redis://redis.internal:6379/0") is None


@pytest.mark.asyncio
async def test_check_redis_or_explain_reports_localhost_ipv6_hint():
    async def fake_verify(url: str) -> None:
        if "localhost" in url:
            raise RuntimeError(f"Redis unreachable at {url}: timed out")

    ok, message = await check_redis_or_explain(
        "redis://localhost:6379/0",
        verify=fake_verify,
    )

    assert ok is False
    assert "localhost" in message
    assert "::1" in message
    assert "127.0.0.1" in message
