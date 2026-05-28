from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Awaitable, Callable, Mapping, Optional, Tuple
from urllib.parse import ParseResult, urlparse

from dotenv import load_dotenv


VerifyRedisFunc = Callable[[str], Awaitable[None]]
REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def should_skip_redis_preflight(env: Mapping[str, str]) -> bool:
    return (
        env.get("STP_SKIP_INFRA_CHECK", "0") == "1"
        and env.get("ENV", "").strip().lower() != "production"
    )


def is_inprocess_saq_enabled(env: Mapping[str, str]) -> bool:
    return env.get("STP_ENABLE_INPROCESS_SAQ", "1") == "1"


def _replace_hostname(parsed: ParseResult, host: str) -> str:
    auth = ""
    if parsed.username:
        auth = parsed.username
        if parsed.password is not None:
            auth += ":{}".format(parsed.password)
        auth += "@"
    elif parsed.password is not None:
        auth = ":{}@".format(parsed.password)

    netloc = "{}{}".format(auth, host)
    if parsed.port is not None:
        netloc = "{}:{}".format(netloc, parsed.port)
    return parsed._replace(netloc=netloc).geturl()


def suggest_ipv4_loopback_url(redis_url: str) -> Optional[str]:
    parsed = urlparse(redis_url)
    if parsed.hostname != "localhost":
        return None
    return _replace_hostname(parsed, "127.0.0.1")


async def _default_verify(redis_url: str) -> None:
    from backend.tasks.saq_worker import verify_redis_connectivity

    await verify_redis_connectivity(redis_url)


async def check_redis_or_explain(
    redis_url: str,
    verify: Optional[VerifyRedisFunc] = None,
) -> Tuple[bool, str]:
    verify = verify or _default_verify
    try:
        await verify(redis_url)
        return True, "Redis preflight OK: {}".format(redis_url)
    except Exception as exc:
        fallback_url = suggest_ipv4_loopback_url(redis_url)
        if fallback_url is not None:
            try:
                await verify(fallback_url)
            except Exception:
                pass
            else:
                return (
                    False,
                    "Redis preflight failed for {}.\n"
                    "Docker Redis is reachable via {}, so the current URL is likely "
                    "hitting Windows localhost IPv6 resolution (::1) first.\n"
                    "Update REDIS_URL in backend\\.env to {} and retry.".format(
                        redis_url,
                        fallback_url,
                        fallback_url,
                    ),
                )

        return (
            False,
            "Redis preflight failed for {}: {}\n"
            "If you use local Docker Redis, run .\\start-redis.bat first.\n"
            "For pure API debugging only, use STP_SKIP_INFRA_CHECK=1 in a non-production shell.".format(
                redis_url,
                exc,
            ),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check backend Redis connectivity before starting uvicorn."
    )
    parser.add_argument(
        "--env-file",
        default="backend/.env",
        help="Path to the backend env file.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_file = Path(args.env_file)
    if env_file.exists():
        load_dotenv(env_file, override=True)

    env = os.environ
    if not is_inprocess_saq_enabled(env):
        print("Redis preflight skipped: STP_ENABLE_INPROCESS_SAQ!=1.")
        return 0
    if should_skip_redis_preflight(env):
        print(
            "Redis preflight skipped: STP_SKIP_INFRA_CHECK=1 in non-production."
        )
        return 0

    redis_url = env.get("REDIS_URL", "redis://localhost:6379/0")
    ok, message = asyncio.run(check_redis_or_explain(redis_url))
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
