"""Agent script verification RPC helpers."""

from __future__ import annotations

import asyncio
from typing import Optional

from backend.realtime.socketio_server import AgentNotConnectedError, AgentRpcError, call_agent_rpc

from . import VERIFY_CONCURRENCY, VERIFY_TIMEOUT_SECONDS


async def verify_one_host(
    host_id: str, expected: list[dict]
) -> tuple[bool, list[dict], Optional[str]]:
    """Returns (ok, scripts_results, error_message)."""
    try:
        ack = await call_agent_rpc(
            host_id,
            "verify_scripts",
            {"expected": expected},
            timeout=VERIFY_TIMEOUT_SECONDS,
        )
    except AgentNotConnectedError:
        return False, [], "agent_offline"
    except AgentRpcError as exc:
        return False, [], f"rpc_failed: {exc}"

    results = list(ack.get("results") or [])
    all_ok = bool(results) and all(r.get("ok") for r in results)
    return all_ok, results, None if all_ok else "sha_mismatch"


async def gather_verify(
    host_ids: list[str], expected: list[dict]
) -> dict[str, tuple[bool, list[dict], Optional[str]]]:
    """对一批主机并发核验；#3422 起并发按 ``VERIFY_CONCURRENCY`` 加界。

    加界原因（生产实测，2026-09-26）：单次 gather 内主机数 ≥30 时 ack 大面积
    超时（而单机粒度调用健康）；admission 全有全无，不设界则大 run 恒不准入。
    每台仍走 ``verify_one_host`` 的独立超时与错误归因，返回结构与顺序不变。
    """
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def _verify(hid: str):
        async with sem:
            return await verify_one_host(hid, expected)

    results = await asyncio.gather(
        *(_verify(hid) for hid in host_ids), return_exceptions=True
    )
    out: dict[str, tuple[bool, list[dict], Optional[str]]] = {}
    for hid, res in zip(host_ids, results, strict=True):
        if isinstance(res, Exception):
            out[hid] = (False, [], f"verify_exception: {res}")
        else:
            out[hid] = res
    return out


_verify_one_host = verify_one_host
_gather_verify = gather_verify
