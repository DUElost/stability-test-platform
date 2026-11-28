"""Agent-side atomic_commit critical-section gate (ADR-0065 / B12 U5 / #4206).

Owns:
- single-process mutex arbitration of ``abort`` vs ``enter``
- Unix-domain-socket ``enter → grant/deny`` / ``exit`` IPC for scripts
- control-plane finishing-guard handshake client
- ≤15s liveness heartbeat after grant
- per-``job_id`` terminal CAS for graceful shutdown

IPC protocol (line-delimited JSON over ``AF_UNIX`` stream socket):

- Script → Agent: ``{"op":"enter","request_id":...,"min_critical_section_seconds":N,
  "step_key":...,"step_timeout_seconds":N}``
- Agent → Script: ``{"op":"grant","request_id":...,"D_atomic":...}`` or
  ``{"op":"deny","request_id":...,"reason":...}``
- Script → Agent: ``{"op":"exit","request_id":...}``

Env: ``STP_CRITICAL_SECTION_SOCK`` = absolute socket path (set by PipelineEngine
before spawning a script that declares ``critical_section_guard``).

Fail-closed: channel error / timeout / deny ⇒ script must not write hardware.
Process crash while only pre-registered ⇒ Agent cancels by ``request_id``.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import requests

from .contracts.finishing_guard import (
    ATOMIC_LIVENESS_FRESH_SECONDS,
    FINISHING_KIND_ATOMIC_COMMIT,
    PRE_REG_GRACE_SECONDS,
)
from .contracts.vbus_flash import deferred_flash_init_prefix_len

logger = logging.getLogger(__name__)

# Local safety margin after activate_permit before delivering grant (seconds).
# Covers RTT + scheduling; must leave room inside the 60s liveness window.
LOCAL_GRANT_SAFETY_MARGIN_SECONDS = 15.0

# Script enter wait budget (fail-closed).
ENTER_HANDSHAKE_TIMEOUT_SECONDS = float(PRE_REG_GRACE_SECONDS)

# Liveness heartbeat interval after grant (ADR-0065 D4.18: ≤15s).
LIVENESS_HEARTBEAT_INTERVAL_SECONDS = 10.0

ENV_CRITICAL_SECTION_SOCK = "STP_CRITICAL_SECTION_SOCK"


# ── terminal CAS (D4.38–D4.41 / C10) ─────────────────────────────────────────


class TerminalCasRegistry:
    """Per-job single-winner terminal settlement slot."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claimed: dict[int, str] = {}

    def try_claim(self, job_id: int, claimant: str) -> bool:
        jid = int(job_id)
        with self._lock:
            existing = self._claimed.get(jid)
            if existing is None:
                self._claimed[jid] = claimant
                return True
            return existing == claimant

    def owner(self, job_id: int) -> Optional[str]:
        with self._lock:
            return self._claimed.get(int(job_id))

    def release(self, job_id: int, claimant: str) -> None:
        jid = int(job_id)
        with self._lock:
            if self._claimed.get(jid) == claimant:
                self._claimed.pop(jid, None)


_GLOBAL_TERMINAL_CAS = TerminalCasRegistry()


def terminal_cas() -> TerminalCasRegistry:
    return _GLOBAL_TERMINAL_CAS


# ── freeze helpers ───────────────────────────────────────────────────────────


def step_identity(step: Mapping[str, Any]) -> str:
    for key in ("step_key", "script_name", "step_id", "id"):
        value = step.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    action = step.get("action")
    if isinstance(action, str) and action.startswith("script:"):
        return action.split(":", 1)[1]
    return ""


def build_atomic_group_frozen(
    init_steps: list[Any],
    *,
    return_check_timeout_seconds: int = 600,
) -> list[dict[str, Any]]:
    """Build ordered second-layer freeze list for pre_register.

    Static ``atomic_group`` members from init, with VBUS/deferred-flash
    ``return_check`` inserted after the deferred flash prefix (ADR-0063 D5.3).
    """
    steps = [s for s in init_steps if isinstance(s, dict) and s.get("enabled") is not False]
    atomic = [s for s in steps if s.get("atomic_group")]
    frozen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _append(step_key: str, timeout: Any, *, source: str) -> None:
        key = (step_key or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        try:
            timeout_i = int(timeout) if timeout is not None else 0
        except (TypeError, ValueError):
            timeout_i = 0
        frozen.append({
            "step_key": key,
            "timeout_seconds": timeout_i,
            "source": source,
        })

    for step in atomic:
        _append(
            step_identity(step),
            step.get("timeout_seconds"),
            source="snapshot",
        )

    prefix = deferred_flash_init_prefix_len(steps)
    if prefix > 0:
        # Insert return_check after flash prefix members in the frozen order.
        insert_at = 0
        prefix_keys = {step_identity(s) for s in steps[:prefix]}
        for i, member in enumerate(frozen):
            if member["step_key"] in prefix_keys:
                insert_at = i + 1
        rc = {
            "step_key": "return_check",
            "timeout_seconds": int(return_check_timeout_seconds),
            "source": "vbus_return_check",
        }
        if "return_check" not in seen:
            frozen.insert(insert_at, rc)
            seen.add("return_check")

    return frozen


def skipped_fails_atomic_delivery(
    step: Mapping[str, Any],
    *,
    skipped: bool,
) -> bool:
    """True when skipped=true must be treated as FAILED inside atomic_group."""
    if not skipped:
        return False
    if step.get("atomic_group") in (None, "", False):
        return False
    return step.get("skip_satisfies_delivery") is not True


# ── HTTP client ──────────────────────────────────────────────────────────────


@dataclass
class FinishingGuardHttpClient:
    api_url: str
    agent_secret: str = ""
    host_id: str = ""
    boot_id: str = ""
    timeout_seconds: float = 10.0
    session_factory: Callable[[], Any] = field(default=requests.Session)

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.agent_secret:
            headers["x-agent-secret"] = self.agent_secret
        return headers

    def _url(self, job_id: int, suffix: str) -> str:
        base = (self.api_url or "").rstrip("/")
        return f"{base}/api/v1/agent/jobs/{int(job_id)}/finishing-guard/{suffix}"

    def pre_register(self, job_id: int, payload: Mapping[str, Any]) -> dict[str, Any]:
        resp = self.session_factory().post(
            self._url(job_id, "pre-register"),
            json=dict(payload),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body) if isinstance(body, dict) else {}

    def activate_permit(
        self, job_id: int, payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        params = {"host_id": self.host_id} if self.host_id else {}
        resp = self.session_factory().post(
            self._url(job_id, "activate-permit"),
            params=params,
            json=dict(payload),
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body) if isinstance(body, dict) else {}

    def cancel_pre_registration(
        self, job_id: int, request_id: str,
    ) -> dict[str, Any]:
        resp = self.session_factory().post(
            self._url(job_id, "cancel-pre-registration"),
            json={"request_id": request_id},
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body) if isinstance(body, dict) else {}

    def heartbeat(
        self,
        job_id: int,
        *,
        request_id: str,
        progress_seq: Optional[int] = None,
        client_time: Optional[float] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": request_id,
            "liveness": True,
            "client_time": client_time if client_time is not None else time.time(),
        }
        if progress_seq is not None:
            payload["progress_seq"] = progress_seq
        resp = self.session_factory().post(
            self._url(job_id, "heartbeat"),
            json=payload,
            headers=self._headers(),
            timeout=self.timeout_seconds,
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body) if isinstance(body, dict) else {}


# ── arbitration + IPC ────────────────────────────────────────────────────────


@dataclass
class GrantDecision:
    granted: bool
    request_id: str
    reason: str = ""
    D_atomic: Optional[float] = None
    permit_id: Optional[str] = None


class CriticalSectionGate:
    """Single mutex arbitration point for abort ↔ enter (ADR-0065 D3.21)."""

    def __init__(
        self,
        *,
        job_id: int,
        http: Optional[FinishingGuardHttpClient] = None,
        safety_margin_seconds: float = LOCAL_GRANT_SAFETY_MARGIN_SECONDS,
        now_fn: Callable[[], float] = time.time,
        lease_remaining_provider: Optional[Callable[[], Optional[float]]] = None,
    ) -> None:
        self.job_id = int(job_id)
        self._http = http
        self._safety = float(safety_margin_seconds)
        self._now = now_fn
        self._lease_remaining_provider = lease_remaining_provider
        self._lock = threading.RLock()
        self.abort_requested = False
        self.force_abort = False
        self.granted = False
        self.hardware_exited = False
        self.request_id: Optional[str] = None
        self.permit_id: Optional[str] = None
        self.D_atomic: Optional[float] = None
        self.progress_seq = 0
        self.atomic_group_completed = False
        self.completed_during_abort_drain = False
        self._liveness_stop = threading.Event()
        self._liveness_thread: Optional[threading.Thread] = None
        self._sock_path: Optional[str] = None
        self._server_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._accept_stop = threading.Event()
        self._frozen_members: list[dict[str, Any]] = []
        self._step_started_at: Optional[float] = None

    # —— abort arbitration ——

    def signal_abort(self, *, force: bool = False) -> None:
        with self._lock:
            self.abort_requested = True
            if force:
                self.force_abort = True

    def should_protect_from_ordinary_kill(self) -> bool:
        with self._lock:
            return self.granted and not self.force_abort

    def should_skip_patrol_after_atomic(self) -> bool:
        with self._lock:
            return self.granted and self.abort_requested and not self.force_abort

    # —— freeze / step bookkeeping ——

    def set_frozen_members(self, members: list[dict[str, Any]]) -> None:
        with self._lock:
            self._frozen_members = list(members)

    def frozen_members(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._frozen_members)

    def mark_step_started(self) -> None:
        with self._lock:
            self._step_started_at = self._now()

    def note_progress(self, seq: Optional[int] = None) -> int:
        with self._lock:
            if seq is not None and seq > self.progress_seq:
                self.progress_seq = int(seq)
            else:
                self.progress_seq += 1
            return self.progress_seq

    def mark_atomic_group_completed(self, *, during_abort_drain: bool) -> None:
        with self._lock:
            self.atomic_group_completed = True
            if during_abort_drain:
                self.completed_during_abort_drain = True

    def terminal_fields(self) -> dict[str, Any]:
        with self._lock:
            if not self.granted and not self.atomic_group_completed:
                return {}
            return {
                "atomic_group_completed": bool(self.atomic_group_completed),
                "completed_during_abort_drain": bool(self.completed_during_abort_drain),
                "finishing_kind": FINISHING_KIND_ATOMIC_COMMIT,
                "abort_mode": (
                    "force" if self.force_abort
                    else ("graceful" if self.abort_requested else "none")
                ),
                "atomic_group_evidence": {
                    "request_id": self.request_id,
                    "frozen": list(self._frozen_members),
                    "hardware_exited": self.hardware_exited,
                },
            }

    # —— enter handshake ——

    def handle_enter(
        self,
        *,
        request_id: Optional[str] = None,
        min_critical_section_seconds: int = 0,
        step_key: str = "",
        step_timeout_seconds: Optional[int] = None,
        atomic_group_frozen: Optional[list[dict[str, Any]]] = None,
    ) -> GrantDecision:
        rid = (request_id or "").strip() or str(uuid.uuid4())
        with self._lock:
            if self.force_abort or self.abort_requested:
                return GrantDecision(
                    granted=False, request_id=rid, reason="abort_already_pending",
                )
            if self.granted:
                return GrantDecision(
                    granted=True,
                    request_id=self.request_id or rid,
                    D_atomic=self.D_atomic,
                    permit_id=self.permit_id,
                    reason="already_granted",
                )
            frozen = list(atomic_group_frozen or self._frozen_members)
            elapsed = 0.0
            if self._step_started_at is not None:
                elapsed = max(0.0, self._now() - self._step_started_at)

        if self._http is None:
            return GrantDecision(granted=False, request_id=rid, reason="no_http_client")

        pre_payload = {
            "request_id": rid,
            "min_critical_section_seconds": int(min_critical_section_seconds or 0),
            "atomic_group_frozen": frozen,
            "current_step_key": step_key or None,
            "step_timeout_seconds": step_timeout_seconds,
            "elapsed_pre_arm_seconds": elapsed,
            "client_boot_id": self._http.boot_id or "",
        }
        try:
            pre = self._http.pre_register(self.job_id, pre_payload)
        except Exception as exc:
            logger.warning(
                "critical_section_pre_register_failed job=%d: %s", self.job_id, exc,
            )
            return GrantDecision(granted=False, request_id=rid, reason="pre_register_failed")

        d_atomic = pre.get("D_atomic")
        permit_id = None
        guard = pre.get("finishing_guard") if isinstance(pre, dict) else None
        if isinstance(guard, dict):
            permit_id = guard.get("permit_id") or guard.get("request_id")
            if d_atomic is None:
                d_atomic = guard.get("D_atomic")

        with self._lock:
            if self.force_abort or self.abort_requested:
                self._safe_cancel(rid)
                return GrantDecision(
                    granted=False, request_id=rid, reason="abort_during_handshake",
                )

        try:
            act = self._http.activate_permit(
                self.job_id,
                {
                    "request_id": rid,
                    "client_boot_id": self._http.boot_id or "",
                },
            )
        except Exception as exc:
            logger.warning(
                "critical_section_activate_failed job=%d: %s", self.job_id, exc,
            )
            self._safe_cancel(rid)
            return GrantDecision(granted=False, request_id=rid, reason="activate_failed")

        if not self._local_margin_ok(act, d_atomic=d_atomic):
            self._safe_cancel(rid)
            return GrantDecision(
                granted=False, request_id=rid, reason="insufficient_local_margin",
            )

        with self._lock:
            if self.force_abort or self.abort_requested:
                self._safe_cancel(rid)
                return GrantDecision(
                    granted=False, request_id=rid, reason="abort_before_grant",
                )
            self.granted = True
            self.request_id = rid
            self.permit_id = str(permit_id or rid)
            self.D_atomic = float(d_atomic) if type(d_atomic) in (int, float) else None
            if frozen:
                self._frozen_members = list(frozen)

        self._start_liveness()
        return GrantDecision(
            granted=True,
            request_id=rid,
            D_atomic=self.D_atomic,
            permit_id=self.permit_id,
        )

    def _local_margin_ok(
        self,
        activate_resp: Mapping[str, Any],
        *,
        d_atomic: Any,
    ) -> bool:
        now = self._now()
        margin = self._safety
        lease_rem = activate_resp.get("lease_remaining_seconds")
        host_rem = activate_resp.get("host_heartbeat_remaining_seconds")
        if type(lease_rem) in (int, float) and float(lease_rem) < margin:
            return False
        if type(host_rem) in (int, float) and float(host_rem) < margin:
            return False
        if type(d_atomic) in (int, float) and float(d_atomic) - now < margin:
            return False
        # Also respect local lease provider if wired (Agent renewer view).
        if self._lease_remaining_provider is not None:
            try:
                local_lease = self._lease_remaining_provider()
            except Exception:
                local_lease = None
            if local_lease is not None and float(local_lease) < margin:
                return False
        # Keep a slice of the 60s liveness window.
        if margin > float(ATOMIC_LIVENESS_FRESH_SECONDS):
            return False
        return True

    def _safe_cancel(self, request_id: str) -> None:
        if self._http is None:
            return
        try:
            self._http.cancel_pre_registration(self.job_id, request_id)
        except Exception as exc:
            logger.warning(
                "critical_section_cancel_failed job=%d rid=%s: %s",
                self.job_id, request_id, exc,
            )

    def cancel_undelivered_grant(self, request_id: str) -> None:
        """Script exited before grant was delivered — revoke pre-registration."""
        with self._lock:
            if self.granted and self.request_id == request_id:
                return
        self._safe_cancel(request_id)

    def note_hardware_exit(self, request_id: Optional[str] = None) -> None:
        with self._lock:
            if request_id and self.request_id and request_id != self.request_id:
                return
            self.hardware_exited = True

    # —— liveness ——

    def _start_liveness(self) -> None:
        if self._liveness_thread is not None:
            return
        self._liveness_stop.clear()

        def _loop() -> None:
            while not self._liveness_stop.wait(LIVENESS_HEARTBEAT_INTERVAL_SECONDS):
                with self._lock:
                    if not self.granted or self._http is None or not self.request_id:
                        return
                    if self.D_atomic is not None and self._now() >= float(self.D_atomic):
                        logger.warning(
                            "critical_section_d_atomic_reached job=%d", self.job_id,
                        )
                        return
                    rid = self.request_id
                    seq = self.progress_seq
                try:
                    self._http.heartbeat(
                        self.job_id, request_id=rid, progress_seq=seq,
                    )
                except Exception as exc:
                    logger.warning(
                        "critical_section_liveness_failed job=%d: %s",
                        self.job_id, exc,
                    )

        self._liveness_thread = threading.Thread(
            target=_loop,
            name=f"cs-liveness-{self.job_id}",
            daemon=True,
        )
        self._liveness_thread.start()

    def stop_liveness(self) -> None:
        self._liveness_stop.set()
        thread = self._liveness_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._liveness_thread = None

    # —— UDS IPC server ——

    def start_ipc_server(self, sock_dir: Optional[str] = None) -> str:
        """Bind a fresh AF_UNIX socket; return path for ``STP_CRITICAL_SECTION_SOCK``."""
        self.stop_ipc_server()
        directory = sock_dir or tempfile.gettempdir()
        path = str(
            Path(directory) / f"stp-cs-{self.job_id}-{uuid.uuid4().hex[:8]}.sock"
        )
        if os.path.exists(path):
            os.unlink(path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(8)
        server.settimeout(0.5)
        self._server_sock = server
        self._sock_path = path
        self._accept_stop.clear()

        def _accept_loop() -> None:
            while not self._accept_stop.is_set():
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._serve_conn,
                    args=(conn,),
                    name=f"cs-ipc-{self.job_id}",
                    daemon=True,
                ).start()

        self._accept_thread = threading.Thread(
            target=_accept_loop, name=f"cs-accept-{self.job_id}", daemon=True,
        )
        self._accept_thread.start()
        return path

    def stop_ipc_server(self) -> None:
        self._accept_stop.set()
        sock = self._server_sock
        self._server_sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        path = self._sock_path
        self._sock_path = None
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        thread = self._accept_thread
        self._accept_thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _serve_conn(self, conn: socket.socket) -> None:
        undelivered_rid: Optional[str] = None
        try:
            conn.settimeout(ENTER_HANDSHAKE_TIMEOUT_SECONDS)
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        self._send(conn, {"op": "deny", "reason": "bad_json"})
                        return
                    op = msg.get("op")
                    if op == "enter":
                        decision = self.handle_enter(
                            request_id=str(msg.get("request_id") or "") or None,
                            min_critical_section_seconds=int(
                                msg.get("min_critical_section_seconds") or 0
                            ),
                            step_key=str(msg.get("step_key") or ""),
                            step_timeout_seconds=(
                                int(msg["step_timeout_seconds"])
                                if msg.get("step_timeout_seconds") is not None
                                else None
                            ),
                        )
                        if decision.granted:
                            undelivered_rid = decision.request_id
                            try:
                                self._send(conn, {
                                    "op": "grant",
                                    "request_id": decision.request_id,
                                    "D_atomic": decision.D_atomic,
                                    "permit_id": decision.permit_id,
                                })
                                undelivered_rid = None
                            except OSError:
                                if undelivered_rid:
                                    self.cancel_undelivered_grant(undelivered_rid)
                                return
                        else:
                            self._send(conn, {
                                "op": "deny",
                                "request_id": decision.request_id,
                                "reason": decision.reason,
                            })
                    elif op == "exit":
                        self.note_hardware_exit(str(msg.get("request_id") or "") or None)
                        self._send(conn, {"op": "ack", "request_id": msg.get("request_id")})
                    else:
                        self._send(conn, {"op": "deny", "reason": "unknown_op"})
        except Exception as exc:
            logger.warning("critical_section_ipc_conn_failed job=%d: %s", self.job_id, exc)
            if undelivered_rid:
                self.cancel_undelivered_grant(undelivered_rid)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send(conn: socket.socket, payload: Mapping[str, Any]) -> None:
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        conn.sendall(data)

    def close(self) -> None:
        self.stop_liveness()
        self.stop_ipc_server()


def script_client_request_enter(
    sock_path: str,
    *,
    request_id: Optional[str] = None,
    min_critical_section_seconds: int = 0,
    step_key: str = "",
    step_timeout_seconds: Optional[int] = None,
    timeout_seconds: float = ENTER_HANDSHAKE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Helper for tests / U6 scripts: blocking enter over the UDS protocol."""
    rid = request_id or str(uuid.uuid4())
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.settimeout(timeout_seconds)
    try:
        conn.connect(sock_path)
        CriticalSectionGate._send(conn, {
            "op": "enter",
            "request_id": rid,
            "min_critical_section_seconds": min_critical_section_seconds,
            "step_key": step_key,
            "step_timeout_seconds": step_timeout_seconds,
        })
        line = b""
        while b"\n" not in line:
            chunk = conn.recv(4096)
            if not chunk:
                break
            line += chunk
        if not line:
            return {"op": "deny", "request_id": rid, "reason": "empty_response"}
        return json.loads(line.split(b"\n", 1)[0].decode("utf-8"))
    finally:
        try:
            conn.close()
        except OSError:
            pass
