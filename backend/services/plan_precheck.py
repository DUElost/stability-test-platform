"""ADR-0021 C3 — Dispatch gate (precheck) implementation.

The dispatch gate runs as a SAQ task triggered by ``POST /plans/{id}/run``.
It walks the PlanRun's ``plan_snapshot`` to derive the host × script matrix,
asks each agent for live sha256s via the ``verify_scripts`` SocketIO RPC,
and on mismatch triggers a host hot-update (rsync + Agent restart) before
re-verifying.  Once all hosts align it materialises JobInstance rows via
:func:`backend.services.plan_dispatcher_sync.complete_plan_run_dispatch`.

Failure modes — all set ``PlanRun.status='FAILED'`` +
``result_summary.precheck_failed=True``:

- agent offline (verify or re-verify)
- sha mismatch after one resync attempt (sync_attempts cap = 1)
- hot-update RPC error during sync
- script entry missing on agent after sync

State is persisted into ``run_context.precheck`` after every transition so
the frontend PlanRunDetailPage can render progress live via SocketIO room
``plan_run:{id}``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.database import SessionLocal
from backend.core.metrics import record_dispatch_gate
from backend.models.host import Device, Host
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.realtime.socketio_server import (
    AgentNotConnectedError,
    AgentRpcError,
    call_agent_rpc,
)
from backend.services.host_updater import execute_hot_update, _resolve_ssh_creds
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    complete_plan_run_dispatch,
)

logger = logging.getLogger(__name__)


VERIFY_TIMEOUT_SECONDS = 10.0
SYNC_SETTLE_SECONDS = 8.0
MAX_SYNC_ATTEMPTS = 1


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _initial_precheck_state(host_ids: list[str]) -> dict:
    return {
        "phase": "verifying",
        "started_at": _utc_iso(),
        "completed_at": None,
        "hosts": {
            hid: {
                "status": "pending",
                "checked_at": None,
                "synced_at": None,
                "scripts": [],
                "sync_attempts": 0,
                "error": None,
            }
            for hid in host_ids
        },
        "final_result": None,
        "errors": [],
    }


def initialise_precheck_state(plan_run_id: int, db: Session) -> dict:
    """Compute (host_id × scripts) coverage from plan_snapshot + device set
    and seed run_context.precheck.  Returns the seeded precheck dict."""
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        raise PlanDispatchError(f"PlanRun {plan_run_id} not found")

    run_ctx = dict(pr.run_context or {})
    device_ids = list(run_ctx.get("dispatch_device_ids") or [])
    if not device_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: run_context.dispatch_device_ids is empty"
        )

    device_rows = db.execute(
        select(Device.id, Device.host_id).where(Device.id.in_(device_ids))
    ).all()
    host_ids = sorted({row.host_id for row in device_rows if row.host_id})

    if not host_ids:
        raise PlanDispatchError(
            f"PlanRun {plan_run_id}: no hosts resolved from devices"
        )

    precheck = _initial_precheck_state(host_ids)
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    db.refresh(pr)
    logger.info(
        "precheck_initialised plan_run=%d hosts=%s", plan_run_id, host_ids
    )
    return precheck


def _expected_scripts_for_run(plan_run: PlanRun, db: Session) -> list[dict]:
    """Build ``[{name, version, sha256, nfs_path}]`` from plan_snapshot ∩
    Script table.  ``content_sha256`` and ``nfs_path`` come from Script —
    plan_snapshot only stores per-step name/version/default_params."""
    snapshot = plan_run.plan_snapshot or {}
    snapshot_steps = snapshot.get("steps") or []
    keys = {(s["script_name"], s["script_version"]) for s in snapshot_steps}
    if not keys:
        return []

    rows = db.execute(
        select(
            Script.name,
            Script.version,
            Script.content_sha256,
            Script.nfs_path,
        ).where(Script.is_active.is_(True))
    ).all()
    matched = [
        {
            "name": r.name,
            "version": r.version,
            "sha256": r.content_sha256 or "",
            "nfs_path": r.nfs_path or "",
        }
        for r in rows
        if (r.name, r.version) in keys
    ]
    return matched


def _persist_precheck(plan_run_id: int, precheck: dict, db: Session) -> None:
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    run_ctx = dict(pr.run_context or {})
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()


async def _verify_one_host(
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


def _sync_host_via_hot_update(host_id: str, db: Session) -> tuple[bool, Optional[str]]:
    """Run ``execute_hot_update`` for a single host using the same SSH
    credential resolution path as the manual hot-update endpoint.

    Returns (ok, error_message).
    """
    host = db.get(Host, host_id)
    if host is None:
        return False, "host_not_found"
    if not host.ip:
        return False, "host_missing_ip"

    extra = host.extra or {}
    ssh_password = extra.get("ssh_password", "")
    ssh_key_path = extra.get("ssh_key_path", "")
    ssh_user = host.ssh_user or "root"

    if not ssh_password and not ssh_key_path:
        inv = _resolve_ssh_creds(host.ip)
        if inv:
            ssh_user = inv["user"]
            ssh_password = inv.get("password", "")

    if not ssh_password and not ssh_key_path:
        return False, "no_ssh_credentials"

    try:
        result = execute_hot_update(
            host_ip=host.ip,
            ssh_port=host.ssh_port or 22,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key_path=ssh_key_path or "",
        )
    except Exception as exc:
        return False, f"hot_update_exception: {exc}"

    if not result.get("ok"):
        return False, f"hot_update_failed: {result.get('message', 'unknown')}"
    return True, None


async def _drive_dispatch_gate(
    plan_run_id: int, *, db: Optional[Session] = None
) -> None:
    """Main async coroutine.  Opens its own DB session by default because
    SAQ tasks don't get a request-scoped session.  Tests may pass an
    existing session via ``db=`` to share state with the test harness.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()
    # ── Prometheus dispatch_gate 指标:全程统计耗时 + 出口 outcome ──
    gate_started_at = time.monotonic()
    gate_outcome = "failed"  # 默认失败,成功路径会显式覆写
    try:
        pr = db.get(PlanRun, plan_run_id)
        if pr is None:
            logger.warning("precheck_no_such_plan_run plan_run=%d", plan_run_id)
            gate_outcome = "skipped"
            return
        if pr.status != "RUNNING":
            logger.info(
                "precheck_skip_non_running plan_run=%d status=%s",
                plan_run_id, pr.status,
            )
            gate_outcome = "skipped"
            return

        precheck = initialise_precheck_state(plan_run_id, db)
        host_ids = list(precheck["hosts"].keys())
        expected_scripts = _expected_scripts_for_run(pr, db)

        if not expected_scripts:
            _mark_precheck_failed(
                plan_run_id, precheck, db,
                error="no_scripts_resolved_for_plan_snapshot",
            )
            return

        # ── Phase 1: verify ───────────────────────────────────────────────
        precheck["phase"] = "verifying"
        _persist_precheck(plan_run_id, precheck, db)
        verify_results = await _gather_verify(host_ids, expected_scripts)

        out_of_sync_hosts: list[str] = []
        for hid in host_ids:
            ok, results, err = verify_results[hid]
            host_state = precheck["hosts"][hid]
            host_state["scripts"] = results
            host_state["checked_at"] = _utc_iso()
            if ok:
                host_state["status"] = "ok"
            else:
                host_state["status"] = "failed" if err == "agent_offline" else "syncing"
                host_state["error"] = err
                if err == "agent_offline":
                    pass  # treated as terminal failure below
                else:
                    out_of_sync_hosts.append(hid)
        _persist_precheck(plan_run_id, precheck, db)

        offline_hosts = [
            hid for hid in host_ids
            if precheck["hosts"][hid]["error"] == "agent_offline"
        ]
        if offline_hosts:
            _mark_precheck_failed(
                plan_run_id, precheck, db,
                error=f"agent_offline: {','.join(offline_hosts)}",
            )
            return

        # ── Phase 2: sync (only mismatched hosts) ─────────────────────────
        if out_of_sync_hosts:
            precheck["phase"] = "syncing"
            _persist_precheck(plan_run_id, precheck, db)

            sync_failures: list[tuple[str, str]] = []
            for hid in out_of_sync_hosts:
                host_state = precheck["hosts"][hid]
                host_state["sync_attempts"] += 1
                if host_state["sync_attempts"] > MAX_SYNC_ATTEMPTS:
                    sync_failures.append((hid, "sync_attempts_exhausted"))
                    host_state["status"] = "failed"
                    host_state["error"] = "sync_attempts_exhausted"
                    continue

                ok_sync, err_sync = await asyncio.to_thread(
                    _sync_host_via_hot_update, hid, db
                )
                if not ok_sync:
                    sync_failures.append((hid, err_sync or "unknown"))
                    host_state["status"] = "failed"
                    host_state["error"] = err_sync
                    continue

                host_state["synced_at"] = _utc_iso()
                host_state["status"] = "synced"
                host_state["error"] = None
                _persist_precheck(plan_run_id, precheck, db)

            if sync_failures:
                msg = "; ".join(f"{h}:{e}" for h, e in sync_failures)
                _mark_precheck_failed(
                    plan_run_id, precheck, db, error=f"sync_failed: {msg}",
                )
                return

            # Give Agent processes time to come back online after restart.
            await asyncio.sleep(SYNC_SETTLE_SECONDS)

            # ── Phase 3: re-verify synced hosts ────────────────────────────
            reverify_results = await _gather_verify(out_of_sync_hosts, expected_scripts)
            still_bad: list[str] = []
            for hid in out_of_sync_hosts:
                ok, results, err = reverify_results[hid]
                host_state = precheck["hosts"][hid]
                host_state["scripts"] = results
                host_state["checked_at"] = _utc_iso()
                if ok:
                    host_state["status"] = "ok"
                    host_state["error"] = None
                else:
                    host_state["status"] = "failed"
                    host_state["error"] = err or "still_mismatch"
                    still_bad.append(hid)
            _persist_precheck(plan_run_id, precheck, db)

            if still_bad:
                _mark_precheck_failed(
                    plan_run_id, precheck, db,
                    error=f"reverify_failed: {','.join(still_bad)}",
                )
                return

        # ── Phase 4: dispatch ─────────────────────────────────────────────
        precheck["phase"] = "ready"
        precheck["final_result"] = "ready"
        precheck["completed_at"] = _utc_iso()
        _persist_precheck(plan_run_id, precheck, db)

        await asyncio.to_thread(complete_plan_run_dispatch, plan_run_id, db)
        logger.info("precheck_dispatched plan_run=%d", plan_run_id)
        # 区分:有 sync 阶段走过 = synced_passed;否则 = passed
        gate_outcome = "synced_passed" if out_of_sync_hosts else "passed"

    except Exception:
        logger.exception("precheck_unexpected_failure plan_run=%d", plan_run_id)
        try:
            pr = db.get(PlanRun, plan_run_id)
            if pr is not None and pr.status == "RUNNING":
                run_ctx = dict(pr.run_context or {})
                precheck = run_ctx.get("precheck") or _initial_precheck_state([])
                precheck["phase"] = "failed"
                precheck["final_result"] = "failed"
                precheck["completed_at"] = _utc_iso()
                precheck.setdefault("errors", []).append("unexpected_exception")
                run_ctx["precheck"] = precheck
                pr.run_context = run_ctx
                pr.status = "FAILED"
                pr.ended_at = datetime.now(timezone.utc)
                pr.result_summary = {
                    "precheck_failed": True,
                    "reason": "unexpected_exception",
                }
                flag_modified(pr, "run_context")
                db.commit()
        except Exception:
            logger.exception("precheck_failure_persist_error plan_run=%d", plan_run_id)
        raise
    finally:
        record_dispatch_gate(gate_outcome, time.monotonic() - gate_started_at)
        if own_session:
            db.close()


async def _gather_verify(
    host_ids: list[str], expected: list[dict]
) -> dict[str, tuple[bool, list[dict], Optional[str]]]:
    coros = [_verify_one_host(hid, expected) for hid in host_ids]
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: dict[str, tuple[bool, list[dict], Optional[str]]] = {}
    for hid, res in zip(host_ids, results):
        if isinstance(res, Exception):
            out[hid] = (False, [], f"verify_exception: {res}")
        else:
            out[hid] = res
    return out


def _mark_precheck_failed(
    plan_run_id: int, precheck: dict, db: Session, *, error: str
) -> None:
    precheck["phase"] = "failed"
    precheck["final_result"] = "failed"
    precheck["completed_at"] = _utc_iso()
    precheck.setdefault("errors", []).append(error)

    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    run_ctx = dict(pr.run_context or {})
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    pr.status = "FAILED"
    pr.ended_at = datetime.now(timezone.utc)
    pr.result_summary = {
        "precheck_failed": True,
        "reason": error,
    }
    flag_modified(pr, "run_context")
    db.commit()
    logger.info("precheck_failed plan_run=%d error=%s", plan_run_id, error)


# ── SAQ task entrypoint ────────────────────────────────────────────────────


async def precheck_and_dispatch_task(ctx: dict, *, plan_run_id: int) -> None:
    """SAQ task: run dispatch gate for ``plan_run_id``."""
    logger.info("saq_precheck_and_dispatch_start plan_run=%d", plan_run_id)
    try:
        await _drive_dispatch_gate(plan_run_id)
    except Exception:
        logger.exception("saq_precheck_and_dispatch_failed plan_run=%d", plan_run_id)
        raise
    logger.info("saq_precheck_and_dispatch_done plan_run=%d", plan_run_id)


# Re-export for tests
drive_dispatch_gate = _drive_dispatch_gate
