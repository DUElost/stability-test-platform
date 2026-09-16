"""
SocketIO server — replaces the legacy ConnectionManager.

Namespaces:
  /agent      — Agent connections: receive logs, step status, heartbeat relay
  /dashboard  — Frontend connections: push device updates, job status, logs

Auth:
  /agent      — X-Agent-Secret header in connect handshake (auth dict)
  /dashboard  — JWT or WS_TOKEN in connect handshake (auth dict)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import socketio
from sqlalchemy import text

from backend.core.agent_secret import AgentSecretNotConfiguredError, require_agent_secret
from backend.core.cors import get_cors_allowed_origins
from backend.core.database import AsyncSessionLocal, SessionLocal
from backend.core.metrics import record_socketio_connection
from backend.core.security import ACCESS_COOKIE_NAME, extract_cookie_token
from backend.services.auth_session import authenticate_token
from backend.services.run_console import RunConsole

logger = logging.getLogger(__name__)


def _authenticate_dashboard_user(token: str):
    """#903 三面校验面（sync）。#1041：必须经 ``asyncio.to_thread`` 执行——
    本模块跑在事件循环上，sync SessionLocal 直连查询会阻塞整个 Socket.IO
    循环；入线程池后与 REST 的 sync 依赖同语义，不阻塞并发握手。"""
    with SessionLocal() as db:
        return authenticate_token(db, token, expected_type="access")


def _origin_allowed(environ: dict) -> bool:
    """服务端强制 Origin 白名单（#904）。

    engineio 的 cors_allowed_origins 只生成 CORS 响应头、由浏览器执行，
    非浏览器客户端可无视；而 Cookie 自动附带握手必带 Origin——故外来
    Origin 必须在应用层拒绝。缺 Origin（脚本/测试携 token）不在此拦，
    交由既有认证分支。
    """
    origin = environ.get("HTTP_ORIGIN", "")
    if not origin:
        return True
    return origin in get_cors_allowed_origins()

def _ws_token() -> str:
    return os.getenv("WS_TOKEN", "")


def _ws_token_configured() -> bool:
    """静态口令旁路是否「显式配置」。

    #281 二轮:源码默认值 ``dev-token-12345`` 不算已配置——未显式设置
    WS_TOKEN 时共享静态口令不再被接受(否则任何部署都有一把公开的
    万能口令,是护栏旁路)。显式设置(自定义值)才放行,生产部署须自行
    配置(.env.backend 已配置独立值)。
    """
    value = _ws_token()
    return bool(value) and value != "dev-token-12345"

_sio: Optional[socketio.AsyncServer] = None
_agent_ns: Optional["AgentNamespace"] = None


class AgentNotConnectedError(Exception):
    """Raised when an RPC targets a host that has no active agent connection."""

    def __init__(self, host_id: str):
        self.host_id = host_id
        super().__init__(f"agent for host '{host_id}' is not connected")


class AgentRpcError(Exception):
    """Raised when an Agent RPC fails (timeout, malformed ack, etc.)."""


def get_sio() -> socketio.AsyncServer:
    """Return the singleton AsyncServer. Raises if not yet created."""
    if _sio is None:
        raise RuntimeError("SocketIO server not initialized — call create_sio_server() first")
    return _sio


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_sio_server() -> socketio.AsyncServer:
    """Create and configure the SocketIO AsyncServer singleton.

    ADR-0027 P3-2: when ``STP_SOCKETIO_REDIS_ADAPTER=1``, attach
    ``AsyncRedisManager`` so room emits fan out across control-plane
    processes. Default remains in-memory (single-process).
    """
    global _sio

    from backend.realtime.socketio_redis import build_socketio_client_manager

    client_manager = build_socketio_client_manager()
    sio_kwargs: Dict[str, Any] = dict(
        async_mode="asgi",
        # #904: 与 FastAPI CORS 同源配置（cors.py 拒绝通配符），不再 "*"
        cors_allowed_origins=get_cors_allowed_origins(),
        logger=False,
        engineio_logger=False,
        ping_timeout=60,
        ping_interval=25,
        max_http_buffer_size=1_000_000,
    )
    if client_manager is not None:
        sio_kwargs["client_manager"] = client_manager

    sio = socketio.AsyncServer(**sio_kwargs)

    _register_agent_namespace(sio)
    _register_dashboard_namespace(sio)
    _sio = sio
    return sio


# ---------------------------------------------------------------------------
# /agent namespace
# ---------------------------------------------------------------------------

class AgentNamespace(socketio.AsyncNamespace):
    """Handles Agent connections on /agent namespace."""

    def __init__(self, namespace: str):
        super().__init__(namespace)
        self._host_to_sid: dict[str, str] = {}
        self._lock = asyncio.Lock()

    def get_sid(self, host_id: str) -> Optional[str]:
        """Return the SocketIO sid for a connected agent, or None."""
        return self._host_to_sid.get(str(host_id))

    def connected_host_ids(self) -> list[str]:
        """Return host_ids with an active connection (testing helper)."""
        return list(self._host_to_sid.keys())

    async def on_connect(self, sid: str, environ: dict, auth: dict | None = None):
        auth = auth or {}
        provided_secret = auth.get("agent_secret", "")
        host_id = auth.get("host_id", "")

        try:
            expected = require_agent_secret()
        except AgentSecretNotConfiguredError:
            logger.warning("agent_sio_rejected sid=%s: AGENT_SECRET not configured", sid)
            raise socketio.exceptions.ConnectionRefusedError("AGENT_SECRET not configured") from None

        if not secrets.compare_digest(provided_secret or "", expected):
            logger.warning("agent_sio_auth_failed sid=%s host_id=%s", sid, host_id)
            raise socketio.exceptions.ConnectionRefusedError("Invalid agent secret")

        if not host_id:
            logger.warning("agent_sio_no_host_id sid=%s", sid)
            raise socketio.exceptions.ConnectionRefusedError("host_id required")

        async with self.session(sid) as session:
            session["host_id"] = host_id

        async with self._lock:
            self._host_to_sid[str(host_id)] = sid

        await self.enter_room(sid, f"agent:{host_id}")
        from backend.realtime.agent_sid_registry import register_agent_owner

        await register_agent_owner(str(host_id), sid)
        record_socketio_connection("/agent", True)
        logger.info("agent_sio_connected sid=%s host_id=%s", sid, host_id)

    async def on_disconnect(self, sid: str):
        async with self.session(sid) as session:
            host_id = session.get("host_id")
        if host_id:
            async with self._lock:
                tracked_sid = self._host_to_sid.get(str(host_id))
                if tracked_sid == sid:
                    self._host_to_sid.pop(str(host_id), None)
            from backend.realtime.agent_sid_registry import unregister_agent_owner

            await unregister_agent_owner(str(host_id), sid)
        record_socketio_connection("/agent", False)
        logger.info("agent_sio_disconnected sid=%s host_id=%s", sid, host_id or "?")

    async def on_step_log(self, sid: str, data: dict):
        """Agent emits step_log → persist to file（#2400 后**只落盘，不再推送**）。

        ADR-0026 P2-2 / #523: batched ``lines: [{step_id, seq, level, ts, msg}, ...]`` only.

        #2400：原先每行还向 ``job:{id}`` / ``run:{id}`` 两个房间各 emit 一次，
        但**前端没有任何订阅方**（订阅工厂 `jobLogsSubscription` / `runLogsSubscription`
        零生产调用点）——Agent 每行日志换两次纯序列化 + 空扇出。行级实时面本就由
        落盘路径承担：`log_writer` → `GET /api/v1/logs/query`（REST 轮询），
        命令式实时输出走 `console:{runId}` 通道（ADR-0025 §9）。要恢复推送需同时
        接上订阅方与房间白名单，见 `tests/test_realtime_wiring_contract.py` 的守卫。
        """
        job_id = data.get("job_id") or data.get("run_id")
        if not job_id:
            return

        raw_lines = data.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            return

        lines = [
            {
                "step_id": item.get("step_id", ""),
                "seq": item.get("seq"),
                "level": item.get("level", "INFO"),
                "ts": item.get("ts", ""),
                "msg": item.get("msg", ""),
            }
            for item in raw_lines
            if isinstance(item, dict)
        ]

        if not lines:
            return

        try:
            from backend.realtime.log_writer import append_log_lines
            await append_log_lines(job_id=int(job_id), lines=lines)
        except Exception:
            logger.debug("log_writer_append_failed job_id=%s", job_id, exc_info=True)

    async def on_job_status(self, sid: str, data: dict):
        """Agent emits intermediate job status (INIT_RUNNING, etc.) → broadcast only, no DB write.

        #2400：原先还向 ``job:{id}`` 房间投一份（无订阅方），已删；run 级订阅走
        ``plan_run:{id}``（前端 `planRunSubscription` 的唯一消费方）。
        """
        job_id = data.get("job_id") or data.get("run_id")
        if not job_id:
            return

        status = data.get("status", "")
        payload = {
            "type": "JOB_STATUS",
            "payload": {
                "job_id": int(job_id),
                "status": status,
                "reason": data.get("reason", ""),
            },
            "timestamp": _now_iso(),
        }

        sio = get_sio()
        run_id = data.get("plan_run_id") or data.get("run_id", job_id)
        await sio.emit("job_status", payload, namespace="/dashboard", room=f"plan_run:{run_id}")

    async def on_heartbeat(self, sid: str, data: dict):
        """Renew Agent SID registry only — no per-device dashboard fan-out (#2324).

        Authoritative device state lands via HTTP ``/api/v1/heartbeat``; the
        former WS per-device ``device_update`` fan-out is deprecated because it
        scaled with host×device and stormed the observation plane.
        ``data`` is accepted for wire compatibility but ignored.
        """
        async with self.session(sid) as session:
            host_id = session.get("host_id", "")

        # Agent 心跳 = 连接活性证据：续租 SID registry（#881——否则连接存活
        # 超过 TTL 后，跨进程 RPC 会因登记过期被拒、预检误判 agent_offline）
        if host_id:
            from backend.realtime.agent_sid_registry import renew_agent_owner

            await renew_agent_owner(str(host_id), sid)


# ---------------------------------------------------------------------------
# /dashboard namespace
# ---------------------------------------------------------------------------

# ── dashboard room 校验（ADR-0029 v2.3 D + #2369 + #2400）───────────────────
# on_subscribe 收窄：格式白名单 + 实体存在性。合法形态 = 后端 emit 端全集：
#   plan_run:    → plan_run.id（job_status / plan_run_status / precheck_update /
#                  watcher_signal 的投递目标）
#   console:     → RunConsole run_id（`con-` + uuid4 hex，进程内态，终态后仍可查）
#   fleet:devices → 静态房间，无实体行（DEVICE_UPDATE 仅扇出给设备页订阅者，#2369）
# #2400 起 **不再有 job:/run: 房间**：它们唯一的 emit 端（Agent step_log 逐行双投）
# 与唯一订阅端（前端 jobLogsSubscription/runLogsSubscription）同时是死角，两边一起删。
# 本白名单与 emit 位的对应关系由 tests/test_realtime_wiring_contract.py 守着——
# 只删一侧或新增 emit 不接线都会被它拦下。
# agent: 是 /agent namespace 内部房间（AgentNamespace 自己 enter_room），
# dashboard 客户端订阅无意义（namespace 隔离），不入白名单。
# 不做归属过滤：REST 面本就允许任意登录用户读任意 run，实时通道不设更严门槛
# （G13 定性：P2 前置一致性 / 健壮性，非越权安全洞）。
_ROOM_PATTERN = re.compile(
    r"^plan_run:[0-9]{1,18}$"
    r"|^console:con-[0-9a-f]{1,32}$"
    r"|^fleet:devices$"
)

FLEET_DEVICES_ROOM = "fleet:devices"


async def _dashboard_room_exists(kind: str, ident: str) -> bool:
    """实体存在性校验。查询失败 fail-closed：无法证明房间有效就不放行——
    订阅被拒只影响推流（重连会重试），不阻塞 REST 主路径。
    """
    if kind == "fleet" and ident == "devices":
        return True
    if kind == "console":
        # #2056：注册表读的是**同步** redis（SOCKET_TIMEOUT_SECONDS=2）——直接在
        # 事件循环里调会把整个 ASGI 冻住最多 2s/次，而重连客户端会密集打这条路径。
        # 挪到线程里执行（同步客户端保持原样，供 ticker/线程路径复用）。
        exists = await asyncio.to_thread(RunConsole.instance().status, ident) is not None
        if not exists:
            # #1114：多实例下「非本实例持有」与「不存在」同路径——留可诊断日志
            try:
                from backend.realtime.socketio_redis import socketio_redis_adapter_enabled
            except Exception:  # pragma: no cover
                socketio_redis_adapter_enabled = lambda: False  # type: ignore[assignment]
            if socketio_redis_adapter_enabled():
                logger.warning(
                    "console_room_refused reason=not_local_multi_instance run_id=%s ref=#1114",
                    ident,
                )
        return exists
    table = "plan_run"
    try:
        async with AsyncSessionLocal() as session:
            row = await session.execute(
                text(f"SELECT 1 FROM {table} WHERE id = :id"),
                {"id": int(ident)},
            )
            return row.first() is not None
    except Exception:
        logger.exception("dashboard_subscribe_entity_check_failed kind=%s", kind)
        return False


class DashboardNamespace(socketio.AsyncNamespace):
    """Handles Frontend connections on /dashboard namespace."""

    async def on_connect(self, sid: str, environ: dict, auth: dict | None = None):
        # #904: 外来 Origin 在认证前直接拒绝——Cookie 自动附带握手必带
        # Origin，有效凭据也不能改变来源不可信这一事实。
        if not _origin_allowed(environ):
            logger.warning(
                "dashboard_sio_origin_rejected sid=%s origin=%s",
                sid,
                environ.get("HTTP_ORIGIN"),
            )
            raise socketio.exceptions.ConnectionRefusedError("Origin not allowed")

        auth = auth or {}
        token = auth.get("token", "")
        if not token:
            token = extract_cookie_token(environ.get("HTTP_COOKIE"), ACCESS_COOKIE_NAME) or ""

        # 除 TESTING=1 外始终要求有效认证(#281 P0):此前仅 ENV=production
        # 拒绝匿名连接,而生产部署实际跑 ENV=internal(.env.backend),护栏
        # 从未生效——无 token 握手即可接入 /dashboard。TESTING=1 下保留
        # 匿名直连供测试套件使用。
        if os.getenv("TESTING") != "1" and not token:
            # Client treats this message as refresh-recoverable (#1119): access
            # cookie gone/expired while refresh may still be valid. Do not
            # loosen auth — only name the refusal for the recovery branch.
            raise socketio.exceptions.ConnectionRefusedError("Authentication required")

        if token:
            if _ws_token_configured() and token == _ws_token():
                pass  # 显式配置的静态口令(#281 二轮:源码默认值不算已配置)
            else:
                try:
                    # R02-D3（#903）：与 REST/metrics 同一校验面（PK 查库 +
                    # is_active + ver 纪元），此前仅签名级 decode——停用用户
                    # 的 token 到 exp 前全通。expected_type="access" 防止
                    # refresh token 经 cookie/auth 旁路冒充 access。
                    # #1041：sync DB 查询经 asyncio.to_thread 进工作线程，
                    # 不阻塞事件循环。DB 故障同样在此转 ConnectionRefused
                    # ——握手 fail-closed（设计 note §6），不降级放行。
                    user = await asyncio.to_thread(_authenticate_dashboard_user, token)
                    if not user:
                        raise socketio.exceptions.ConnectionRefusedError("Invalid token")
                except socketio.exceptions.ConnectionRefusedError:
                    raise
                except Exception:
                    raise socketio.exceptions.ConnectionRefusedError("Invalid token") from None

        record_socketio_connection("/dashboard", True)
        logger.info("dashboard_sio_connected sid=%s", sid)

    async def on_disconnect(self, sid: str):
        record_socketio_connection("/dashboard", False)
        logger.info("dashboard_sio_disconnected sid=%s", sid)

    async def on_subscribe(self, sid: str, data: dict):
        """Client subscribes to specific rooms (job logs, plan runs, etc.).

        ADR-0029 v2.3 D：格式白名单 + 实体存在性校验，不合法的 room 拒绝进入
        （不 enter_room，记 WARNING）。合法形态见 ``_ROOM_PATTERN``；不存在
        实体的房间不会收到任何 emit，订阅它只会堆积无意义 room 条目。
        """
        room = data.get("room", "")
        # #1112: reject non-string rooms (e.g. Map refcount numbers) without
        # TypeError from ``_ROOM_PATTERN.fullmatch``.
        if not isinstance(room, str):
            logger.warning(
                "dashboard_subscribe_rejected_type sid=%s room=%r", sid, room,
            )
            return
        if not room:
            return
        if _ROOM_PATTERN.fullmatch(room) is None:
            logger.warning("dashboard_subscribe_rejected_format sid=%s room=%r", sid, room)
            return
        kind, ident = room.split(":", 1)
        if not await _dashboard_room_exists(kind, ident):
            logger.warning("dashboard_subscribe_rejected_entity sid=%s room=%r", sid, room)
            return
        await self.enter_room(sid, room)
        logger.debug("dashboard_subscribe sid=%s room=%s", sid, room)

    async def on_unsubscribe(self, sid: str, data: dict):
        """Client unsubscribes from a room."""
        room = data.get("room", "")
        if not isinstance(room, str) or not room:
            return
        await self.leave_room(sid, room)
        logger.debug("dashboard_unsubscribe sid=%s room=%s", sid, room)


def _register_agent_namespace(sio: socketio.AsyncServer) -> None:
    global _agent_ns
    _agent_ns = AgentNamespace("/agent")
    sio.register_namespace(_agent_ns)


def _register_dashboard_namespace(sio: socketio.AsyncServer) -> None:
    sio.register_namespace(DashboardNamespace("/dashboard"))


def get_agent_namespace() -> "AgentNamespace":
    """Return the registered AgentNamespace instance.

    Raises ``RuntimeError`` if SocketIO has not been initialised yet.
    """
    if _agent_ns is None:
        raise RuntimeError(
            "AgentNamespace not registered — call create_sio_server() first"
        )
    return _agent_ns


async def call_agent_rpc(
    host_id: str,
    event: str,
    data: dict,
    *,
    timeout: float = 10.0,
) -> dict:
    """Invoke an RPC on a connected agent and await its ack response.

    Internally uses ``sio.call(event, data, ...)`` which relies on the
    SocketIO ack mechanism.  The agent's handler must ``return`` the
    response value for it to be auto-forwarded as the ack payload.

    Routing (ADR-0027 P3-3):
    - Prefer process-local ``host_id → sid`` when the Agent is on this
      process.
    - Otherwise, with Redis adapter (+ optional sid registry), call via
      room ``agent:{host_id}`` so the owning instance delivers the RPC —
      LB sticky is no longer required when those flags are on.

    Raises:
        RuntimeError: if SocketIO has not been initialised.
        AgentNotConnectedError: if no agent is currently connected for ``host_id``.
        AgentRpcError: on RPC timeout or transport-level failure.
    """
    sio = get_sio()
    ns = get_agent_namespace()
    sid = ns.get_sid(host_id)
    call_kwargs: Dict[str, Any] = {
        "namespace": "/agent",
        "timeout": timeout,
    }
    if sid:
        call_kwargs["to"] = sid
    else:
        from backend.realtime.agent_sid_registry import (
            agent_sid_registry_enabled,
            lookup_agent_owner,
        )
        from backend.realtime.socketio_redis import socketio_redis_adapter_enabled

        if not socketio_redis_adapter_enabled():
            raise AgentNotConnectedError(str(host_id))
        if agent_sid_registry_enabled():
            owner = await lookup_agent_owner(host_id)
            if owner is None:
                raise AgentNotConnectedError(str(host_id))
        call_kwargs["room"] = f"agent:{host_id}"

    try:
        ack = await sio.call(event, data, **call_kwargs)
    except asyncio.TimeoutError as exc:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' timed out after {timeout}s"
        ) from exc
    except Exception as exc:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' failed: {exc}"
        ) from exc

    if ack is None:
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' returned no ack payload"
        )
    if not isinstance(ack, dict):
        raise AgentRpcError(
            f"agent rpc '{event}' to host '{host_id}' returned non-dict ack: "
            f"{type(ack).__name__}"
        )
    return ack


# ---------------------------------------------------------------------------
# Broadcast helpers (replacement for websocket.py broadcast_* functions)
# ---------------------------------------------------------------------------

async def broadcast_device_update(device_data: Dict[str, Any]) -> None:
    """Push a DEVICE_UPDATE to fleet:devices subscribers only (#2369).

    Not namespace-global: AppShell / Dashboard no longer need per-device frames
    after dashboard_summary (#2324). Devices page opts in via room subscribe.
    """
    sio = get_sio()
    await sio.emit("device_update", {
        "type": "DEVICE_UPDATE",
        "payload": device_data,
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=FLEET_DEVICES_ROOM)


async def broadcast_dashboard_summary(summary: Dict[str, Any]) -> None:
    """Push a coalesced DASHBOARD_SUMMARY to all dashboard subscribers (#2324)."""
    sio = get_sio()
    await sio.emit("dashboard_summary", {
        "type": "DASHBOARD_SUMMARY",
        "payload": summary,
        "timestamp": _now_iso(),
    }, namespace="/dashboard")


async def broadcast_run_job_update(run_id: int, job_id: int, status: str) -> None:
    """Notify frontend that a specific job's status changed."""
    sio = get_sio()
    await sio.emit("job_status", {
        "type": "JOB_STATUS",
        "payload": {"job_id": job_id, "status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"plan_run:{run_id}")


async def broadcast_plan_run_status(run_id: int, status: str) -> None:
    """Notify frontend that the overall PlanRun reached a terminal status."""
    sio = get_sio()
    await sio.emit("plan_run_status", {
        "type": "PLAN_RUN_STATUS",
        "payload": {"status": status},
        "timestamp": _now_iso(),
    }, namespace="/dashboard", room=f"plan_run:{run_id}")


async def broadcast_watcher_signal(
    run_id: int,
    *,
    job_id: int,
    device_serial: Optional[str],
    category: str,
    inserted_count: int = 1,
) -> None:
    """ADR-0021 C5c — push watcher anomaly increment to plan_run subscribers.

    Frontend uses this purely as an *invalidation hint* — it triggers a
    refetch of `/plan-runs/{id}/watcher-summary` rather than mutating the
    cached payload directly.  This keeps the event payload tiny and avoids
    drift between the in-memory aggregate and the server-side window query.
    """
    sio = get_sio()
    await sio.emit(
        "watcher_signal",
        {
            "type": "WATCHER_SIGNAL",
            "payload": {
                "job_id": int(job_id),
                "device_serial": device_serial,
                "category": category,
                "inserted_count": int(inserted_count),
            },
            "timestamp": _now_iso(),
        },
        namespace="/dashboard",
        room=f"plan_run:{run_id}",
    )


# Thread-safe synchronous emit bridge (for recycler and other sync callers)

_main_loop: Optional[asyncio.AbstractEventLoop] = None


def capture_main_loop() -> None:
    """Store the main event loop reference for thread-safe emit."""
    global _main_loop
    _main_loop = asyncio.get_event_loop()


def schedule_emit(event: str, data: Dict[str, Any], namespace: str = "/dashboard", room: str | None = None) -> None:
    """Thread-safe emit — callable from any thread (recycler, etc.)."""
    if _main_loop is None or _main_loop.is_closed():
        logger.warning("main_loop_not_available_for_sio_emit")
        return
    try:
        sio = get_sio()
    except RuntimeError:
        logger.warning("sio_not_initialized_for_emit")
        return
    coro = sio.emit(event, data, namespace=namespace, room=room)
    asyncio.run_coroutine_threadsafe(coro, _main_loop)


# #703：大批量 abort 时若对每个 host 各调一次 schedule_emit，会向主循环同步
# 提交 N 个 run_coroutine_threadsafe；上百 host 时事件循环被瞬时灌满。
# 合并为单协程扇出，并在批次间 sleep(0) 让出调度。
_ABORT_CONTROL_FANOUT_YIELD_EVERY = 8


def schedule_agent_control_fanout(
    items: list[tuple[str, Dict[str, Any]]],
    *,
    yield_every: int = _ABORT_CONTROL_FANOUT_YIELD_EVERY,
) -> None:
    """Thread-safe：一次提交，向多个 agent room 发 control。

    ``items`` 为 ``(host_id, data)``，``data`` 即 sio.emit 的事件体
    （含 ``command`` / ``payload``）。空列表为 no-op。
    """
    if not items:
        return
    if _main_loop is None or _main_loop.is_closed():
        logger.warning("main_loop_not_available_for_sio_emit")
        return
    try:
        sio = get_sio()
    except RuntimeError:
        logger.warning("sio_not_initialized_for_emit")
        return

    async def _fanout() -> None:
        failures = 0
        for i, (host_id, data) in enumerate(items):
            # #1926：per-item 容错——单 emit 失败（如 Redis adapter publish
            # 异常）不得终止整个扇出协程，否则剩余 host 永远收不到 abort
            # control（job 滞留 RUNNING 只能等 reaper 兜底）。
            try:
                await sio.emit(
                    "control",
                    data,
                    namespace="/agent",
                    room=f"agent:{host_id}",
                )
            except Exception:  # noqa: BLE001 - 扇出隔离：逐项记录继续
                failures += 1
                logger.exception("agent_control_fanout_emit_failed host=%s", host_id)
            if yield_every > 0 and (i + 1) % yield_every == 0:
                await asyncio.sleep(0)
        if failures:
            logger.error(
                "agent_control_fanout_partial_failures total=%d failed=%d",
                len(items), failures,
            )

    future = asyncio.run_coroutine_threadsafe(_fanout(), _main_loop)

    # #1926：future 不再静默丢弃——协程级异常（per-item 已隔离，这里是
    # 框架性失败）留业务日志而非 "never retrieved" 噪音。
    def _log_future_exception(f: asyncio.Future) -> None:
        if f.cancelled():
            return
        exc = f.exception()
        if exc is not None:
            logger.error("agent_control_fanout_coroutine_failed: %s", exc)

    future.add_done_callback(_log_future_exception)


def emit_plan_changed(plan_id: int, action: str) -> None:
    """Sync-safe:任一浏览器创建/更新/删除 Plan 后广播 plan_changed,
    其余端据此失效计划缓存(#268 多Worker B2——此前 Plan 编辑跨端陈旧最长 60s+)。"""
    schedule_emit("plan_changed", {
        "type": "PLAN_CHANGED",
        "payload": {"plan_id": plan_id, "action": action},
        "timestamp": _now_iso(),
    })


def emit_project_changed(project_id: int, action: str) -> None:
    """Sync-safe:项目 facet / 归档 / 设备归属变更后广播 project_changed（ADR-0029 D8、#406）。

    前端据此失效 projects / project / devices 缓存，避免 A 端移出设备后
    B 端陈旧缓存一路放行到派发。
    """
    schedule_emit("project_changed", {
        "type": "PROJECT_CHANGED",
        "payload": {"project_id": project_id, "action": action},
        "timestamp": _now_iso(),
    })


async def emit_agent_control(host_id: str, command: str, *, payload: Optional[Dict[str, Any]] = None) -> None:
    """向指定 host 的 Agent 下发控制指令(经 SocketIO /agent control 事件)。

    要求调用方在 async 上下文(后端端点/SAQ task)中执行——emit 直接 await 可达。
    独立进程(如 sync 的 dispatch/recycler)请改用 asyncio 无关的 schedule_emit。
    """
    sio = get_sio()
    await sio.emit("control", {
        "command": command,
        "payload": payload or {},
    }, namespace="/agent", room=f"agent:{host_id}")
    logger.info("emit_agent_control host_id=%s command=%s", host_id, command)


def call_agent_control_sync(
    host_id: str,
    command: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 3.0,
) -> bool:
    """线程安全版 call_agent_control：ack 等待桥接到主事件循环。

    sio 连接对象属于主循环——工作线程（asyncio.to_thread 里的服务工具等）
    不得用 asyncio.run 新建循环跨循环 emit（不受支持，可能静默丢弃）。
    返回 True = Agent 侧 control handler 已确认收到。

    **只能从非主循环线程调用**：在主循环线程里调用会自等 future 而死锁；
    已在 async 上下文的调用方直接 await call_agent_control。
    """
    if _main_loop is None or _main_loop.is_closed():
        logger.warning("main_loop_not_available_for_agent_control host=%s", host_id)
        return False
    # sio 未初始化的 RuntimeError 由协程体内抛出（get_agent_namespace），
    # 在 future.result() 处统一收口——此处不做无效的构造期捕获
    coro = call_agent_control(host_id, command, payload=payload, timeout=timeout)
    future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    try:
        return future.result(timeout=timeout + 2.0)
    except Exception as exc:  # noqa: BLE001 - 桥接失败等价于未送达
        logger.warning("call_agent_control_sync_failed host=%s err=%s", host_id, exc)
        return False


async def call_agent_control(
    host_id: str,
    command: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    timeout: float = 3.0,
) -> bool:
    """下发 control 命令并等待 Agent ack（P2-4）。

    返回 True 仅表示「已送达」（Agent 侧 control handler 已收到，可能仍在
    队列中），不代表命令执行完成。Agent 离线/超时/无本地 sid 时返回 False，
    调用方（scan_task）据此记录缺口或重发。
    """
    ns = get_agent_namespace()
    sid = ns.get_sid(host_id)
    call_kwargs: Dict[str, Any] = {
        "namespace": "/agent",
        "timeout": timeout,
    }
    if sid:
        call_kwargs["to"] = sid
    else:
        try:
            from backend.realtime.socketio_redis import socketio_redis_adapter_enabled
        except Exception:
            socketio_redis_adapter_enabled = lambda: False  # type: ignore[assignment]
        if not socketio_redis_adapter_enabled():
            logger.warning("call_agent_control_no_sid host=%s command=%s", host_id, command)
            return False
        call_kwargs["room"] = f"agent:{host_id}"

    sio = get_sio()
    try:
        ack = await sio.call(
            "control",
            {"command": command, "payload": payload or {}},
            **call_kwargs,
        )
    except asyncio.TimeoutError:
        logger.warning("call_agent_control_timeout host=%s command=%s", host_id, command)
        return False
    except Exception as exc:
        logger.warning("call_agent_control_failed host=%s command=%s err=%s", host_id, command, exc)
        return False
    return isinstance(ack, dict) and ack.get("ok") is True
