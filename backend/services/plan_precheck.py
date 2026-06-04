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
- sha mismatch after ``DISPATCH_SYNC_MAX_ATTEMPTS`` resync attempts (default 1)
- hot-update RPC error during sync
- script entry missing on agent after sync

State is persisted into ``run_context.precheck`` after every transition so
the frontend PlanRunDetailPage can render progress live via SocketIO room
``plan_run:{id}``.

Additionally, ``run_context.dispatch_state`` is kept in sync at each major
boundary so that the ``precheck_reaper`` can determine whether a precheck job
has been lost, swept, or stalled, and recover accordingly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.core.database import SessionLocal
from backend.core.metrics import record_dispatch_gate
from backend.core.ssh_security import (
    SshSecurityConfigError,
    create_ssh_client,
    resolve_host_ssh_credentials,
)
from backend.models.host import Device, Host
from backend.models.plan_run import PlanRun
from backend.models.script import Script
from backend.realtime.socketio_server import (
    AgentNotConnectedError,
    AgentRpcError,
    call_agent_rpc,
)
from backend.services.host_updater import execute_hot_update, _resolve_ssh_creds, _AGENT_SOURCE_DIR
from backend.services.plan_dispatcher_sync import (
    PlanDispatchError,
    complete_plan_run_dispatch,
    initial_dispatch_state,
)

logger = logging.getLogger(__name__)


VERIFY_TIMEOUT_SECONDS = 10.0
SYNC_SETTLE_SECONDS = 8.0
DISPATCH_SYNC_MAX_ATTEMPTS = max(1, int(os.getenv("DISPATCH_SYNC_MAX_ATTEMPTS", "1")))


def _utc_iso() -> str:
    """Return UTC now as ISO-8601 string with Z suffix.

    >>> _utc_iso()
    '2026-05-10T06:45:36.712Z'

    Note: do NOT use ``datetime.now(timezone.utc).isoformat() + 'Z'`` —
    that produces ``+00:00Z`` which PostgreSQL cannot parse.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


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
        "sync_max_attempts": DISPATCH_SYNC_MAX_ATTEMPTS,
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


def _emit_dispatch_gate_invalidation(
    plan_run_id: int,
    *,
    phase: str | None = None,
    dispatch_status: str | None = None,
) -> None:
    """Push a coarse invalidation hint to the plan_run SocketIO room."""
    try:
        from backend.realtime.socketio_server import schedule_emit
    except Exception:
        return
    try:
        schedule_emit(
            "precheck_update",
            {
                "type": "PRECHECK_UPDATE",
                "payload": {
                    "phase": phase,
                    "dispatch_status": dispatch_status,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            namespace="/dashboard",
            room=f"plan_run:{plan_run_id}",
        )
    except Exception:
        logger.debug("emit_dispatch_gate_invalidation_failed", exc_info=True)


def _persist_precheck(plan_run_id: int, precheck: dict, db: Session) -> None:
    pr = db.get(PlanRun, plan_run_id)
    if pr is None:
        return
    run_ctx = dict(pr.run_context or {})
    run_ctx["precheck"] = precheck
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    _emit_dispatch_gate_invalidation(plan_run_id, phase=precheck.get("phase"))


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

    try:
        creds, _migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
    except SshSecurityConfigError as exc:
        return False, f"ssh_security_config_error: {exc}"
    if not creds.password and not creds.key_path:
        return False, "no_ssh_credentials"

    try:
        result = execute_hot_update(
            host_ip=host.ip,
            ssh_port=host.ssh_port or 22,
            ssh_user=creds.user,
            ssh_password=creds.password,
            ssh_key_path=creds.key_path,
            known_hosts_path=creds.known_hosts_path,
        )
    except Exception as exc:
        return False, f"hot_update_exception: {exc}"

    if not result.get("ok"):
        return False, f"hot_update_failed: {result.get('message', 'unknown')}"
    return True, None


def _update_dispatch_state(pr: PlanRun, db: Session, **patch: object) -> None:
    """Persist incremental changes to ``run_context.dispatch_state``.

    Updates are committed immediately so the reaper always sees the
    latest authoritative state from the SAQ worker process.
    """
    run_ctx = dict(pr.run_context or {})
    state = dict(run_ctx.get("dispatch_state") or {})
    state.update(patch)
    run_ctx["dispatch_state"] = state
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()
    dispatch_status = state.get("status")
    if isinstance(dispatch_status, str):
        precheck = run_ctx.get("precheck") or {}
        phase = precheck.get("phase") if isinstance(precheck, dict) else None
        _emit_dispatch_gate_invalidation(
            pr.id,
            phase=phase if isinstance(phase, str) else None,
            dispatch_status=dispatch_status,
        )


# ── 轻量级定向脚本同步（仅推送 mismatched 文件，无需重启 Agent）──

# remote prefix that maps to local _AGENT_SOURCE_DIR
_REMOTE_AGENT_PREFIX = "/opt/stability-test-agent/agent/"


def _nfs_path_to_local(nfs_path: str) -> str | None:
    """Map an agent-side nfs_path to a local (backend-side) file path.

    Only handles scripts under the standard agent install prefix.
    Returns None for paths outside that tree (e.g. custom NFS mounts).
    """
    if not nfs_path.startswith(_REMOTE_AGENT_PREFIX):
        return None
    rel = nfs_path[len(_REMOTE_AGENT_PREFIX):]
    local = str(_AGENT_SOURCE_DIR / rel)
    return local


def _get_ssh_client(host_ip: str, host_ssh_port: int, ssh_user: str,
                    ssh_password: str, ssh_key_path: str,
                    known_hosts_path: str = ""):
    """Create a connected paramiko SSH client."""
    return create_ssh_client(
        hostname=host_ip,
        port=host_ssh_port,
        username=ssh_user,
        password=ssh_password,
        key_path=ssh_key_path,
        known_hosts_path=known_hosts_path,
        timeout=15,
    )


def _push_mismatched_scripts(
    host_id: str, mismatched: list[dict], db: Session,
) -> tuple[bool, str | None]:
    """SFTP only the mismatched script files to the agent host.

    ``mismatched`` is a list of ``{name, version, nfs_path, sha256}``
    entries from ``_expected_scripts_for_run`` for scripts where the
    agent's actual sha256 differed.
    """
    host = db.get(Host, host_id)
    if host is None:
        return False, "host_not_found"
    if not host.ip:
        return False, "host_missing_ip"

    try:
        creds, _migrated = resolve_host_ssh_credentials(
            host, inventory_lookup=_resolve_ssh_creds,
        )
    except SshSecurityConfigError as exc:
        return False, f"ssh_security_config_error: {exc}"
    if not creds.password and not creds.key_path:
        return False, "no_ssh_credentials"

    import paramiko

    pushed = 0
    failed: list[str] = []
    client = None
    try:
        client = _get_ssh_client(
            host.ip, host.ssh_port or 22, creds.user,
            creds.password, creds.key_path, creds.known_hosts_path,
        )
        sftp = client.open_sftp()

        for script in mismatched:
            nfs_path = script.get("nfs_path", "")
            local_path = _nfs_path_to_local(nfs_path)
            if not local_path:
                failed.append(f"{script['name']}: cannot map nfs_path")
                continue

            import os
            if not os.path.isfile(local_path):
                failed.append(f"{script['name']}: local file not found: {local_path}")
                continue

            # Ensure remote directory exists
            remote_dir = os.path.dirname(nfs_path)
            try:
                sftp.stat(remote_dir)
            except FileNotFoundError:
                # create directory tree
                parts = remote_dir.lstrip("/").split("/")
                path = ""
                for p in parts:
                    path = f"{path}/{p}"
                    try:
                        sftp.stat(path)
                    except FileNotFoundError:
                        sftp.mkdir(path)

            # Upload the file
            try:
                sftp.put(local_path, nfs_path)
                pushed += 1
            except Exception as exc:
                failed.append(f"{script['name']}: SFTP failed: {exc}")

            # Also push _adb.py helper from the same local directory if present
            local_dir = os.path.dirname(local_path)
            adb_helper = os.path.join(local_dir, "_adb.py")
            if os.path.isfile(adb_helper):
                adb_remote = os.path.join(remote_dir, "_adb.py")
                try:
                    sftp.put(adb_helper, adb_remote)
                except Exception:
                    pass  # best-effort, script may still import from another location

        sftp.close()

        if pushed > 0:
            # Fix CRLF from Windows sources and ensure executable
            cmd_parts = []
            for script in mismatched:
                nfs = script.get("nfs_path", "")
                if nfs:
                    cmd_parts.append(f"sed -i 's/\\r$//' '{nfs}' 2>/dev/null || true")
                    cmd_parts.append(f"chmod 755 '{nfs}' 2>/dev/null || true")
            if cmd_parts:
                client.exec_command("; ".join(cmd_parts))

    except Exception as exc:
        return False, f"ssh_exception: {exc}"
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass

    if failed:
        return False, f"partial_fail: pushed={pushed}, failed={'; '.join(failed)}"

    logger.info(
        "lightweight_sync_done host=%s pushed=%d scripts=%s",
        host_id, pushed, [s["name"] for s in mismatched],
    )
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

        # Mark dispatch_state as running now that we've picked up the job.
        _update_dispatch_state(
            pr, db,
            status="running",
            started_at=_utc_iso(),
            last_error=None,
        )

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

        # ── Phase 2+3: sync (with optional retry) + re-verify ────────────
        hosts_to_sync = list(out_of_sync_hosts)
        while hosts_to_sync:
            precheck["phase"] = "syncing"
            _persist_precheck(plan_run_id, precheck, db)

            sync_failures: list[tuple[str, str]] = []
            for hid in hosts_to_sync:
                host_state = precheck["hosts"][hid]
                host_state["sync_attempts"] += 1
                host_state["status"] = "syncing"
                host_state["error"] = None

                scripts = host_state.get("scripts") or []
                mismatched_names = {
                    s["name"] for s in scripts
                    if not s.get("ok")
                }
                mismatched_entries = [
                    es for es in expected_scripts
                    if es["name"] in mismatched_names
                ]

                if not mismatched_entries:
                    sync_failures.append((hid, "no_mismatched_entries"))
                    host_state["status"] = "failed"
                    host_state["error"] = "no_mismatched_entries"
                    continue

                ok_sync, err_sync = await asyncio.to_thread(
                    _push_mismatched_scripts, hid, mismatched_entries, db
                )
                if ok_sync:
                    host_state["synced_at"] = _utc_iso()
                    host_state["status"] = "synced"
                    host_state["error"] = None
                else:
                    logger.warning(
                        "lightweight_sync_failed host=%s error=%s — falling back to hot-update",
                        hid, err_sync,
                    )
                    ok_hot, err_hot = await asyncio.to_thread(
                        _sync_host_via_hot_update, hid, db
                    )
                    if ok_hot:
                        host_state["synced_at"] = _utc_iso()
                        host_state["status"] = "synced"
                        host_state["error"] = None
                    else:
                        sync_failures.append((hid, err_hot or "hot_update_failed"))
                        host_state["status"] = "failed"
                        host_state["error"] = err_hot or "hot_update_failed"

                _persist_precheck(plan_run_id, precheck, db)

            if sync_failures:
                retry_hosts = [
                    h for h, _ in sync_failures
                    if precheck["hosts"][h]["sync_attempts"] < DISPATCH_SYNC_MAX_ATTEMPTS
                ]
                if retry_hosts:
                    logger.info(
                        "precheck_sync_retry plan_run=%d hosts=%s",
                        plan_run_id, retry_hosts,
                    )
                    hosts_to_sync = retry_hosts
                    continue
                msg = "; ".join(f"{h}:{e}" for h, e in sync_failures)
                _mark_precheck_failed(
                    plan_run_id, precheck, db, error=f"sync_failed: {msg}",
                )
                return

            precheck["phase"] = "reverifying"
            _persist_precheck(plan_run_id, precheck, db)
            await asyncio.sleep(SYNC_SETTLE_SECONDS)

            reverify_results = await _gather_verify(hosts_to_sync, expected_scripts)
            still_bad: list[str] = []
            for hid in hosts_to_sync:
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
                retry_hosts = [
                    h for h in still_bad
                    if precheck["hosts"][h]["sync_attempts"] < DISPATCH_SYNC_MAX_ATTEMPTS
                ]
                if retry_hosts:
                    logger.info(
                        "precheck_reverify_retry plan_run=%d hosts=%s",
                        plan_run_id, retry_hosts,
                    )
                    hosts_to_sync = retry_hosts
                    continue
                _mark_precheck_failed(
                    plan_run_id, precheck, db,
                    error=f"reverify_failed: {','.join(still_bad)}",
                )
                return
            break

        # ── Phase 4: dispatch ─────────────────────────────────────────────
        precheck["phase"] = "ready"
        precheck["final_result"] = "ready"
        precheck["completed_at"] = _utc_iso()
        _persist_precheck(plan_run_id, precheck, db)

        await asyncio.to_thread(complete_plan_run_dispatch, plan_run_id, db)

        # ADR-0023 C1 阶段 2 协调:complete_plan_run_dispatch 在 keys 校验失败时
        # 不抛异常,而是内部写入 PlanRun.status='FAILED' + result_summary。
        # 重读 status 决策 dispatch_state 与 gate_outcome,避免被默认的 ready 分支覆盖。
        db.expire(pr)
        pr = db.get(PlanRun, plan_run_id)
        if pr is not None and pr.status == "FAILED":
            result_summary = pr.result_summary or {}
            missing = result_summary.get("missing_scripts") or []
            last_error = (
                f"dispatch_failed: {','.join(missing)}"
                if missing else "dispatch_failed"
            )
            _update_dispatch_state(
                pr, db,
                status="failed",
                completed_at=_utc_iso(),
                last_error=last_error,
            )
            gate_outcome = "failed"
            logger.info(
                "precheck_dispatch_failed plan_run=%d missing=%s",
                plan_run_id, missing,
            )
            return

        logger.info("precheck_dispatched plan_run=%d", plan_run_id)
        # 区分:有 sync 阶段走过 = synced_passed;否则 = passed
        gate_outcome = "synced_passed" if out_of_sync_hosts else "passed"

        _update_dispatch_state(
            pr, db,
            status="completed",
            completed_at=_utc_iso(),
            last_error=None,
        )

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
                _emit_dispatch_gate_invalidation(
                    plan_run_id,
                    phase="failed",
                    dispatch_status="failed",
                )
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

    # Keep dispatch_state in sync so the reaper won't double-process this run.
    dispatch_state = dict(run_ctx.get("dispatch_state") or {})
    dispatch_state["status"] = "failed"
    dispatch_state["completed_at"] = _utc_iso()
    dispatch_state["last_error"] = f"precheck:{error}"
    run_ctx["dispatch_state"] = dispatch_state

    db.commit()
    _emit_dispatch_gate_invalidation(
        plan_run_id,
        phase="failed",
        dispatch_status="failed",
    )
    logger.info("precheck_failed plan_run=%d error=%s", plan_run_id, error)


class PlanRunDispatchRetryError(Exception):
    """Raised when manual precheck/dispatch retry is not allowed."""


def retry_plan_run_dispatch(
    run_id: int,
    db: Session,
    *,
    triggered_by: str,
) -> dict:
    """Reset a failed precheck PlanRun and re-enqueue the dispatch gate."""
    from backend.models.job import JobInstance
    from backend.tasks.saq_worker import EnqueueSyncError, enqueue_sync

    pr = db.get(PlanRun, run_id)
    if pr is None:
        raise PlanRunDispatchRetryError("plan run not found")

    has_jobs = (
        db.query(JobInstance.id)
        .filter(JobInstance.plan_run_id == run_id)
        .first()
    )
    if has_jobs:
        raise PlanRunDispatchRetryError(
            "plan run already has jobs; cannot retry dispatch"
        )

    run_ctx = dict(pr.run_context or {})
    if not run_ctx.get("dispatch_device_ids"):
        raise PlanRunDispatchRetryError("missing dispatch_device_ids")

    summary = dict(pr.result_summary or {})
    precheck = run_ctx.get("precheck") or {}
    dispatch_state = run_ctx.get("dispatch_state") or {}
    eligible = (
        pr.status == "FAILED" and summary.get("precheck_failed")
    ) or (
        pr.status == "RUNNING"
        and (
            precheck.get("phase") == "failed"
            or dispatch_state.get("status") == "failed"
        )
    )
    if not eligible:
        raise PlanRunDispatchRetryError(
            f"plan run not eligible for dispatch retry (status={pr.status})"
        )

    pr.status = "RUNNING"
    pr.ended_at = None
    pr.result_summary = None

    new_dispatch = initial_dispatch_state()
    new_dispatch["enqueue_key"] = f"precheck:{run_id}"
    run_ctx["dispatch_state"] = new_dispatch
    pr.run_context = run_ctx
    flag_modified(pr, "run_context")
    db.commit()

    initialise_precheck_state(run_id, db)

    try:
        enqueue_sync(
            "precheck_and_dispatch_task",
            key=f"precheck:{run_id}",
            timeout=600,
            retries=1,
            required=True,
            plan_run_id=run_id,
        )
    except EnqueueSyncError as exc:
        now = datetime.now(timezone.utc)
        pr = db.get(PlanRun, run_id)
        if pr is not None:
            pr.status = "FAILED"
            pr.ended_at = now
            run_ctx = dict(pr.run_context or {})
            dispatch_state = dict(run_ctx.get("dispatch_state") or {})
            dispatch_state["status"] = "failed"
            dispatch_state["last_error"] = str(exc)
            dispatch_state["completed_at"] = _utc_iso()
            run_ctx["dispatch_state"] = dispatch_state
            pr.run_context = run_ctx
            pr.result_summary = {
                "precheck_failed": True,
                "reason": "dispatch_queue_unavailable",
                "error": str(exc),
            }
            flag_modified(pr, "run_context")
            db.commit()
        raise PlanRunDispatchRetryError(
            f"dispatch queue unavailable: {exc}"
        ) from exc

    logger.info(
        "plan_run_dispatch_retry plan_run=%d triggered_by=%s",
        run_id,
        triggered_by,
    )
    return {
        "plan_run_id": run_id,
        "status": "RUNNING",
        "dispatch_state": new_dispatch,
    }


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
