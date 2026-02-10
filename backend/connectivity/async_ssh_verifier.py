import asyncio
import time
from typing import Any, Dict, List, Optional

import asyncssh

from .error_handler import RetryConfig


async def verify_ssh_async(
    host: str,
    port: int = 22,
    username: Optional[str] = None,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    timeout: float = 5.0,
    retry: Optional[RetryConfig] = None,
) -> Dict[str, Any]:
    start = time.time()
    attempt = 0
    while True:
        try:
            async with asyncssh.connect(
                host=host,
                port=port,
                username=username,
                password=password,
                client_keys=[key_path] if key_path else None,
                known_hosts=None,
                login_timeout=timeout,
            ):
                pass
            return {
                "ok": True,
                "host": host,
                "latency_ms": int((time.time() - start) * 1000),
            }
        except Exception as exc:
            if not retry or attempt >= retry.retries:
                return {
                    "ok": False,
                    "host": host,
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                }
            delay = min(retry.base_delay * (retry.backoff**attempt), retry.max_delay)
            if retry.jitter:
                delay += retry.jitter
            await asyncio.sleep(delay)
            attempt += 1


async def verify_hosts(
    hosts: List[Dict[str, Any]],
    retry: Optional[RetryConfig] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    sem = asyncio.Semaphore(limit)

    async def _wrap(h: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            return await verify_ssh_async(
                host=h.get("host"),
                port=h.get("port", 22),
                username=h.get("username"),
                password=h.get("password"),
                key_path=h.get("key_path"),
                timeout=h.get("timeout", 5.0),
                retry=retry,
            )

    tasks = [_wrap(h) for h in hosts]
    return await asyncio.gather(*tasks)
