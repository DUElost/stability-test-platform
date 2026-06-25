"""Agent script verification RPC helpers."""

from __future__ import annotations

import asyncio
from typing import Optional

from backend.realtime.socketio_server import AgentNotConnectedError, AgentRpcError

from . import VERIFY_TIMEOUT_SECONDS


async def verify_one_host(
    host_id: str, expected: list[dict]
) -> tuple[bool, list[dict], Optional[str]]:
    """Returns (ok, scripts_results, error_message)."""
    import backend.services.plan_precheck as plan_precheck_facade

    try:
        ack = await plan_precheck_facade.call_agent_rpc(
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
    coros = [verify_one_host(hid, expected) for hid in host_ids]
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: dict[str, tuple[bool, list[dict], Optional[str]]] = {}
    for hid, res in zip(host_ids, results):
        if isinstance(res, Exception):
            out[hid] = (False, [], f"verify_exception: {res}")
        else:
            out[hid] = res
    return out


_verify_one_host = verify_one_host
_gather_verify = gather_verify
