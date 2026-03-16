from __future__ import annotations

import asyncio
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

from backend.api.routes import auth_router, heartbeat_router, hosts_router, tasks_router
from backend.api.routes.devices import router as devices_router
from backend.api.routes.websocket import router as websocket_router, capture_main_loop
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.users import router as users_router
from backend.api.routes.results import router as results_router
from backend.api.routes.stats import router as stats_router
from backend.api.routes.notifications import router as notifications_router
from backend.api.routes.audit import router as audit_router
from backend.api.routes.schedules import router as schedules_router
from backend.api.routes.templates import router as templates_router
from backend.api.routes.pipeline import router as pipeline_router
from backend.api.routes.builtin_actions import router as builtin_actions_router
# Phase 3: new routers replace legacy workflows + tools
from backend.api.routes.orchestration import router as orchestration_router
from backend.api.routes.tool_catalog import router as tool_catalog_router
from backend.api.routes.action_templates import router as action_templates_router
from backend.api.routes.agent_api import router as agent_api_router
from backend.core.database import async_engine, engine
from backend.core.limiter import RateLimitMiddleware
from backend.core.metrics import init_build_info
from backend.mq.consumer import consume_log_stream, consume_status_stream, monitor_backpressure
from backend.services.state_machine import InvalidTransitionError
from backend.tasks.heartbeat_monitor import heartbeat_monitor_loop
from backend.tasks.session_watchdog import USE_SESSION_WATCHDOG, session_watchdog_loop
from backend.scheduler.cron_scheduler import start_cron_scheduler

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

_STREAM_GROUPS = [
    ("stp:status",  "server-consumer"),
    ("stp:logs",    "log-consumer"),
    ("stp:control", "agent-consumer"),
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    cron_thread = None

    if os.getenv("TESTING") != "1":
        # Connect Redis
        redis_client = await aioredis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            encoding="utf-8",
            decode_responses=True,
        )
        for stream, group in _STREAM_GROUPS:
            try:
                await redis_client.xgroup_create(stream, group, id="0", mkstream=True)
            except Exception:
                pass  # already exists

        monitor_task = None
        if not USE_SESSION_WATCHDOG:
            monitor_task = asyncio.create_task(heartbeat_monitor_loop())
            logger.info("legacy_heartbeat_monitor_enabled")
        watchdog_task = None
        if USE_SESSION_WATCHDOG:
            watchdog_task = asyncio.create_task(session_watchdog_loop())
            logger.info("session_watchdog_enabled")
        mq_consumer_task = asyncio.create_task(consume_status_stream(redis_client))
        mq_log_task = asyncio.create_task(consume_log_stream(redis_client))
        mq_bp_task = asyncio.create_task(monitor_backpressure(redis_client))
        capture_main_loop()
        init_build_info(version="2.0.0", commit="unknown")
        if os.getenv("ENABLE_CRON_SCHEDULER", "1") == "1":
            cron_thread = start_cron_scheduler()
            logger.info("cron_scheduler_thread_started name=%s", getattr(cron_thread, "name", "cron-scheduler"))

    yield

    if os.getenv("TESTING") != "1":
        if monitor_task:
            monitor_task.cancel()
        if watchdog_task:
            watchdog_task.cancel()
        mq_consumer_task.cancel()
        mq_log_task.cancel()
        mq_bp_task.cancel()
        if redis_client:
            await redis_client.aclose()
        await async_engine.dispose()


app = FastAPI(title="Stability Test Platform", lifespan=lifespan)


@app.exception_handler(InvalidTransitionError)
async def invalid_transition_handler(request: Request, exc: InvalidTransitionError):
    return JSONResponse(
        status_code=409,
        content={"data": None, "error": {"code": "INVALID_TRANSITION", "message": str(exc)}},
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"data": None, "error": {"code": "INTERNAL_ERROR", "message": "Internal server error"}})
app.add_middleware(RateLimitMiddleware)

# CORS: restrict to specific origins in production
# Use CORS_ORIGINS env var (comma-separated) or default to common dev origins
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
allow_origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(heartbeat_router)
app.include_router(hosts_router)
app.include_router(tasks_router)
app.include_router(devices_router)
app.include_router(websocket_router)
app.include_router(metrics_router)
app.include_router(users_router)
app.include_router(results_router)
app.include_router(stats_router)
app.include_router(notifications_router)
app.include_router(audit_router)
app.include_router(schedules_router)
app.include_router(templates_router)
app.include_router(pipeline_router)
app.include_router(builtin_actions_router)
# Phase 3 routers (replace legacy tools + workflows)
app.include_router(tool_catalog_router)
app.include_router(action_templates_router)
app.include_router(orchestration_router)
app.include_router(agent_api_router)


@app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "2.0.0"}


@app.get("/health")
async def health_check():
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"data": {"status": "healthy"}, "error": None}
    except Exception:
        return JSONResponse(status_code=503, content={"data": None, "error": {"code": "DB_UNAVAILABLE", "message": "database disconnected"}})
