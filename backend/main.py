from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# 加载 .env 文件（指定 backend 目录）
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

import socketio as python_socketio

from backend.api.routes import auth_router, heartbeat_router, hosts_router
from backend.api.routes.devices import router as devices_router
from backend.api.routes.runs import router as runs_router
from backend.api.routes.logs import router as logs_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.users import router as users_router
from backend.api.routes.results import router as results_router
from backend.api.routes.stats import router as stats_router
from backend.api.routes.notifications import router as notifications_router
from backend.api.routes.audit import router as audit_router
from backend.api.routes.schedules import router as schedules_router
from backend.api.routes.pipeline import router as pipeline_router
from backend.api.routes.scripts import router as scripts_router
from backend.api.routes.action_templates import router as action_templates_router
from backend.api.routes.agent_api import router as agent_api_router
from backend.api.routes.resource_pools import router as resource_pools_router
# ADR-0020: Plan-based orchestration
from backend.api.routes.plans import router as plans_router
from backend.api.routes.plan_runs import router as plan_runs_router
from backend.api.routes.dedup import router as dedup_router
from backend.api.routes.dedup import scan_router as dedup_scan_router
from backend.core.agent_secret import (
    AgentSecretNotConfiguredError,
    is_agent_secret_configured,
    require_agent_secret,
)
from backend.core.cors import get_cors_allowed_origins, get_cors_config
from backend.core.csrf import CSRFOriginMiddleware, is_csrf_enabled
from backend.core.database import async_engine, engine
from backend.core.limiter import RateLimitMiddleware
from backend.core.metrics import init_build_info
from backend.core.security import validate_production_auth_cookie_settings
from backend.realtime.socketio_server import create_sio_server, capture_main_loop
from backend.services.state_machine import InvalidTransitionError
from backend.scheduler.app_scheduler import create_scheduler, register_schedules
from backend.tasks.saq_worker import (
    is_saq_ready,
    start_saq_worker,
    stop_saq_worker,
    verify_redis_connectivity,
)

logger = logging.getLogger(__name__)

# Patch uvicorn loggers to include timestamps while preserving colors
from uvicorn.logging import AccessFormatter, DefaultFormatter

_datefmt = "%Y-%m-%d %H:%M:%S"
for _ln in ("uvicorn", "uvicorn.error"):
    for _h in logging.getLogger(_ln).handlers:
        _h.setFormatter(DefaultFormatter(
            "%(asctime)s %(levelprefix)s %(message)s",
            datefmt=_datefmt,
            use_colors=True,
        ))
for _h in logging.getLogger("uvicorn.access").handlers:
    _h.setFormatter(AccessFormatter(
        '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        datefmt=_datefmt,
        use_colors=True,
    ))

redis_client: Optional[aioredis.Redis] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    scheduler = None

    if os.getenv("TESTING") != "1":
        validate_production_auth_cookie_settings()
        get_cors_config()
        try:
            require_agent_secret()
        except AgentSecretNotConfiguredError as exc:
            raise RuntimeError("AGENT_SECRET required when TESTING!=1") from exc
        logger.info(
            "startup_security_config testing=%s agent_secret_configured=%s",
            os.getenv("TESTING"), is_agent_secret_configured(),
        )

        # Redis — retained for SAQ broker (task queue)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = await aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

        capture_main_loop()
        init_build_info(version="2.0.0", commit="unknown")

        # ADR-0025 §9: RunConsole（控制面命令执行 + web 实时控制台）配置
        from backend.services.run_console import RunConsole
        RunConsole.instance().configure(
            log_root=os.getenv("STP_RUN_CONSOLE_LOG_ROOT", "logs/console"),
            encoding=os.getenv("STP_DEDUP_LOG_ENCODING", "utf-8"),
        )

        # APScheduler replaces legacy daemon threads + asyncio background tasks
        scheduler = create_scheduler()
        await scheduler.__aenter__()
        await register_schedules(scheduler)
        await scheduler.start_in_background()
        logger.info("apscheduler_started")

        # SAQ async task queue (post-completion, notifications, control commands)
        # Controlled by STP_ENABLE_INPROCESS_SAQ — disable in production when
        # you want to run SAQ as a standalone worker process instead of
        # in-process (avoids orphan jobs on hot-reload).
        ENABLE_INPROCESS_SAQ = os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1"
        skip_infra = (
            os.getenv("STP_SKIP_INFRA_CHECK", "0") == "1"
            and os.getenv("ENV", "").strip().lower() != "production"
        )
        if ENABLE_INPROCESS_SAQ:
            if skip_infra:
                logger.warning(
                    "infra_check_skipped_by_env STP_SKIP_INFRA_CHECK=1 "
                    "(Redis PING + in-process SAQ skipped)"
                )
            else:
                try:
                    await verify_redis_connectivity(redis_url)
                    logger.info("redis_ping_ok url=%s", redis_url)
                except RuntimeError as exc:
                    logger.error("redis_unreachable — %s", exc)
                    raise
                try:
                    await start_saq_worker()
                except Exception as exc:
                    logger.error("saq_worker_start_failed — %s", exc)
                    raise RuntimeError(f"SAQ worker failed to start: {exc}") from exc
                # ADR-0026 Step 4.1: pump readiness requires BOTH the pump
                # schedule (registered above) AND a live SAQ executor —
                # otherwise prepare could mint QUEUED runs that only churn
                # QUEUED↔PRECHECK with nothing to admit them.
                from backend.core.admission_queue import mark_queue_pump_ready
                mark_queue_pump_ready(True)
        else:
            # No in-process SAQ → no admission executor in this process; the
            # pump stays unready (V2 prepare stays legacy; the pump tick
            # short-circuits). Revisit when the SAQ producer/worker split
            # lands (ADR-0026 落地顺序 P0 剩余项).
            logger.warning("saq_worker_disabled_by_env")

    yield

    if os.getenv("TESTING") != "1":
        # ADR-0025 §9: RunConsole 收尾——cancel inflight subprocess 避免孤儿
        from backend.services.run_console import RunConsole
        try:
            RunConsole.instance().shutdown()
        except Exception:
            logger.exception("run_console_shutdown_failed")
        # ADR-0026: pump 随进程退出 — 立即撤销就绪标记,防止 shutdown 窗口内
        # 新的 V2 QUEUED 产生却无人准入。
        from backend.core.admission_queue import mark_queue_pump_ready
        mark_queue_pump_ready(False)
        await stop_saq_worker()
        if scheduler is not None:
            await scheduler.__aexit__(None, None, None)
            logger.info("apscheduler_stopped")
        if redis_client:
            await redis_client.aclose()
        await async_engine.dispose()


_fastapi_app = FastAPI(title="Stability Test Platform", lifespan=lifespan)
fastapi_app = _fastapi_app  # Exposed for tests and tooling

sio_server = create_sio_server()
app = python_socketio.ASGIApp(sio_server, _fastapi_app)


@_fastapi_app.exception_handler(InvalidTransitionError)
async def invalid_transition_handler(request: Request, exc: InvalidTransitionError):
    return JSONResponse(
        status_code=409,
        content={"data": None, "error": {"code": "INVALID_TRANSITION", "message": str(exc)}},
    )


@_fastapi_app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"data": None, "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}})
# 中间件注册顺序遵循 Starlette LIFO:最先 add 的在请求链最内层。
# 期望请求链:CORS(最外,确保 4xx 也带 CORS 头) → RateLimit → CSRF(最内,贴近路由)
_fastapi_app.add_middleware(
    CSRFOriginMiddleware,
    allowed_origins=get_cors_allowed_origins(),
    enabled=is_csrf_enabled(),
)
_fastapi_app.add_middleware(RateLimitMiddleware)

_fastapi_app.add_middleware(
    CORSMiddleware,
    **get_cors_config(),
)

_fastapi_app.include_router(auth_router)
_fastapi_app.include_router(heartbeat_router)
_fastapi_app.include_router(hosts_router)
_fastapi_app.include_router(runs_router)
_fastapi_app.include_router(logs_router)
_fastapi_app.include_router(devices_router)
_fastapi_app.include_router(metrics_router)
_fastapi_app.include_router(users_router)
_fastapi_app.include_router(results_router)
_fastapi_app.include_router(stats_router)
_fastapi_app.include_router(notifications_router)
_fastapi_app.include_router(audit_router)
_fastapi_app.include_router(schedules_router)
_fastapi_app.include_router(pipeline_router)
_fastapi_app.include_router(scripts_router)
_fastapi_app.include_router(action_templates_router)
_fastapi_app.include_router(agent_api_router)
_fastapi_app.include_router(resource_pools_router)
# ADR-0020: Plan-based orchestration
_fastapi_app.include_router(plans_router)
_fastapi_app.include_router(plan_runs_router)
_fastapi_app.include_router(dedup_router)
_fastapi_app.include_router(dedup_scan_router)


@_fastapi_app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "2.0.0"}


@_fastapi_app.get("/health")
async def health_check():
    inprocess_saq = os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1"
    saq_ready: bool | None = is_saq_ready() if inprocess_saq else None
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        payload: dict = {"status": "healthy"}
        if inprocess_saq:
            payload["saq_ready"] = saq_ready
        return {"data": payload, "error": None}
    except Exception:
        return JSONResponse(status_code=503, content={"data": None, "error": {"code": "DB_UNAVAILABLE", "message": "database disconnected"}})
