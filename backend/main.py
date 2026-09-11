from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

# env 加载顺序（三层都是 override=False，先到先得）：
#   1. 进程已有的环境变量 —— systemd 的 EnvironmentFile 走这条，永远最优先
#   2. 仓库根 .env.backend —— **生产唯一事实源**。显式加载它，使手工
#      `uvicorn backend.main:app` 与 systemd 启动落到同一套配置
#   3. backend/.env —— 仅供本地开发覆盖 1、2 都没提供的键
#
# 第 2 层是 2026-08-01 补的：此前只加载 backend/.env，而那份文件的
# DATABASE_URL / JWT_SECRET_KEY / SSH_CREDENTIALS_FERNET_KEY / AGENT_SECRET /
# REDIS_URL 都与生产不同。systemd 启动时靠 ambient 覆盖侥幸没出事，但任何
# 手工启动都会静默落到另一套配置上（连不通的库、解不开的 SSH 凭据、
# 对不上的会话）。
_repo_root = Path(__file__).resolve().parent.parent
# #884（R01-F04）：TESTING=1 下跳过 dotenv 加载 —— 测试不得读取或复用
# .env.backend（docs/development/testing.md §2）。门控在配置加载层，
# 见 backend/core/env_source.load_app_dotenv。
from backend.core.env_source import load_app_dotenv  # noqa: E402

load_app_dotenv(_repo_root)

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

import socketio as python_socketio

from backend.api.routes import auth_router, heartbeat_router, hosts_router
from backend.api.routes.ai_assistant import router as ai_assistant_router
from backend.api.routes.devices import router as devices_router
from backend.api.routes.jobs import router as jobs_router
from backend.api.routes.runs import router as runs_router
from backend.api.routes.logs import router as logs_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.users import router as users_router
from backend.api.routes.results import router as results_router
from backend.api.routes.projects import router as projects_router
from backend.api.routes.stats import router as stats_router
from backend.api.routes.notifications import router as notifications_router
from backend.api.routes.audit import router as audit_router
from backend.api.routes.schedules import router as schedules_router
from backend.api.routes.settings import router as settings_router
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
# P0: MTBF 工具 API（runtask.xml 预览/校验，ADR-0030 / docs/operations/mtbf-api.md）
from backend.api.routes.mtbf import router as mtbf_router
# P1a: MTBF 套件/用例管理面（ADR-0030 D1/D6，外部 agent REST 入口）
from backend.api.routes.suites import router as test_suites_router
from backend.core.agent_secret import (
    AgentSecretNotConfiguredError,
    is_agent_secret_configured,
    require_agent_secret,
)
from backend.core.cors import get_cors_allowed_origins, get_cors_config
from backend.core.csrf import CSRFOriginMiddleware, is_csrf_enabled
from backend.core.database import async_engine
from backend.core.limiter import RateLimitMiddleware
from backend.core.metrics import init_build_info
from backend.core.redis import redact_redis_url
from backend.core.security import is_production_like_env, validate_production_auth_cookie_settings
from backend.realtime.socketio_server import create_sio_server, capture_main_loop
from backend.services.state_machine import InvalidTransitionError
from backend.scheduler.app_scheduler import create_scheduler, register_schedules
from backend.tasks.saq_worker import (
    is_saq_ready,
    start_saq_worker,
    stop_saq_worker,
    stop_saq_producer,
    init_saq_producer,
    verify_redis_connectivity,
)

logger = logging.getLogger(__name__)

# #563: give backend.** a stdout handler. Without this every app-level
# logger.info() fell through to logging.lastResort (stderr, WARNING+ only),
# so periodic sweeps and startup registration left no trace in production.
from backend.core.logging_setup import configure_logging

configure_logging()

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

# Readiness 探针的 Redis ping 时限（#1177）：与 saq_worker.REDIS_PING_TIMEOUT
# 同 env 同缺省——黑洞分区（SYN 丢弃）下 ping 不得悬挂超过 Docker HEALTHCHECK
# 时限，否则探针任务无限累积。
_HEALTH_REDIS_PING_TIMEOUT = float(os.getenv("REDIS_PING_TIMEOUT", "3.0"))


def _log_redis_ping_ok(redis_url: str) -> None:
    """Emit the startup Redis success record without credential material."""
    logger.info("redis_ping_ok url=%s", redact_redis_url(redis_url))


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

        # #1114（R11-F06 / ADR-0027 清单第 6 条）：多实例模式下 RunConsole 仍是
        # 进程内态——dedup Jira 串行 / Agent 安装 console / 助手 console 动作与日志 /
        # console 房间订阅为单实例语义；使用这些功能应保持单实例（或 LB 层 sticky）。
        from backend.services.run_console import multi_instance_console_warning

        _console_warning = multi_instance_console_warning()
        if _console_warning:
            logger.warning(_console_warning)

        # Redis — retained for SAQ broker (task queue)
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        redis_client = await aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
        )

        try:
            from backend.realtime.agent_sid_registry import configure_agent_sid_registry

            configure_agent_sid_registry(redis_client)

            capture_main_loop()
            init_build_info(version="2.0.0", commit="unknown")

            # ADR-0025 §9: RunConsole（控制面命令执行 + web 实时控制台）配置
            from backend.services.run_console import RunConsole
            RunConsole.instance().configure(
                log_root=os.getenv("STP_RUN_CONSOLE_LOG_ROOT", "logs/console"),
                encoding=os.getenv("STP_DEDUP_LOG_ENCODING", "utf-8"),
            )

            # R01-F03（#883）：依赖校验先行，再启动有副作用的后台任务——
            # 此前 Scheduler 先起、Redis/SAQ 校验在后，启动异常会留下已启动
            # 的 Scheduler 而清理段（裸 yield 之后）不执行。
            ENABLE_INPROCESS_SAQ = os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1"
            skip_infra = (
                os.getenv("STP_SKIP_INFRA_CHECK", "0") == "1"
                and not is_production_like_env()
            )
            if skip_infra:
                logger.warning(
                    "infra_check_skipped_by_env STP_SKIP_INFRA_CHECK=1 "
                    "(Redis PING + SAQ producer/worker skipped)"
                )
            else:
                try:
                    await verify_redis_connectivity(redis_url)
                    _log_redis_ping_ok(redis_url)
                except RuntimeError as exc:
                    logger.error("redis_unreachable — %s", exc)
                    raise
                try:
                    # SAQ async task queue (post-completion, notifications,
                    # control commands). ADR-0026 P0: producer is always
                    # initialised when Redis is reachable.
                    # ``STP_ENABLE_INPROCESS_SAQ=0`` skips only the in-process
                    # worker so an external worker can drain the same Redis
                    # queue without paralysing enqueue / admission pump.
                    if ENABLE_INPROCESS_SAQ:
                        await start_saq_worker()
                    else:
                        await init_saq_producer()
                        logger.warning(
                            "saq_inprocess_worker_disabled — producer ready; "
                            "expect an external SAQ worker on queue=%s",
                            os.getenv("SAQ_QUEUE_NAME", "stp"),
                        )
                except Exception as exc:
                    logger.error("saq_start_failed — %s", exc)
                    raise RuntimeError(f"SAQ failed to start: {exc}") from exc
                # Pump readiness: live in-process worker OR external-worker mode
                # with a connected producer (ADR-0026 P0 producer/worker split).
                from backend.core.admission_queue import mark_queue_pump_ready
                if is_saq_ready():
                    mark_queue_pump_ready(True)

            # APScheduler last（#883）：纯调度面，其前置依赖（Redis/SAQ）已就绪
            scheduler = create_scheduler()
            try:
                await scheduler.__aenter__()
            except Exception:
                logger.exception("apscheduler_enter_failed")
                scheduler = None
                raise
            try:
                await register_schedules(scheduler)
                await scheduler.start_in_background()
                logger.info("apscheduler_started")
            except Exception:
                # 回滚已进入的 scheduler；__aexit__ 自身失败不吞原始异常
                try:
                    await scheduler.__aexit__(None, None, None)
                except Exception:
                    logger.exception("apscheduler_stop_failed")
                scheduler = None
                raise
        except Exception:
            # R01-F03（#883）：任一启动阶段失败，清理已启动的资源再传播
            await _lifespan_cleanup(scheduler)
            redis_client = None
            raise

    try:
        yield
    finally:
        if os.getenv("TESTING") != "1":
            # R01-F03（#883）：try/finally 保证关闭段必然执行
            await _lifespan_cleanup(scheduler)
            redis_client = None


async def _lifespan_cleanup(scheduler) -> None:
    """#883（R01-F03）：lifespan 清理段——每步独立容错，单步失败不阻断后续清理。"""
    # ADR-0025 §9: RunConsole 收尾——cancel inflight subprocess 避免孤儿
    from backend.services.run_console import RunConsole
    try:
        RunConsole.instance().shutdown()
    except Exception:
        logger.exception("run_console_shutdown_failed")
    # ADR-0026: pump 随进程退出 — 立即撤销就绪标记,防止 shutdown 窗口内
    # 新的 V2 QUEUED 产生却无人准入。
    from backend.core.admission_queue import mark_queue_pump_ready
    try:
        mark_queue_pump_ready(False)
    except Exception:
        logger.exception("pump_ready_revoke_failed")
    try:
        if os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1":
            await stop_saq_worker()
        else:
            await stop_saq_producer()
    except Exception:
        logger.exception("saq_stop_failed")
    if scheduler is not None:
        try:
            await scheduler.__aexit__(None, None, None)
            logger.info("apscheduler_stopped")
        except Exception:
            logger.exception("apscheduler_stop_failed")
    global redis_client
    if redis_client:
        try:
            await redis_client.aclose()
        except Exception:
            logger.exception("redis_close_failed")
    try:
        await async_engine.dispose()
    except Exception:
        logger.exception("engine_dispose_failed")


def _api_docs_enabled(raw: str | None = None) -> bool:
    """G22: STP_API_DOCS_ENABLED 解析，缺省开启（现状行为不变）。

    与 core/security.py / api/routes/metrics.py 同款白名单语义
    （1/true/yes/on）；未设置或空串视为未配置 → 缺省开。
    /docs、/redoc、/openapi.json 属同一暴露面：schema 本身就描述了全部
    端点与鉴权形态，只遮 HTML 不删 openapi.json 没有意义，三者必须同开关。
    """
    value = ((os.getenv("STP_API_DOCS_ENABLED") if raw is None else raw) or "").strip().lower()
    return not value or value in {"1", "true", "yes", "on"}


_API_DOCS = _api_docs_enabled()
if not _API_DOCS:
    logger.info(
        "api_docs_disabled_by_env — /docs /redoc /openapi.json are off "
        "(STP_API_DOCS_ENABLED); bearer token flow at POST /api/v1/auth/token is unaffected"
    )

_fastapi_app = FastAPI(
    title="Stability Test Platform",
    lifespan=lifespan,
    docs_url="/docs" if _API_DOCS else None,
    redoc_url="/redoc" if _API_DOCS else None,
    openapi_url="/openapi.json" if _API_DOCS else None,
)
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
_fastapi_app.include_router(jobs_router)
_fastapi_app.include_router(runs_router)
_fastapi_app.include_router(logs_router)
_fastapi_app.include_router(devices_router)
_fastapi_app.include_router(metrics_router)
_fastapi_app.include_router(users_router)
_fastapi_app.include_router(results_router)
_fastapi_app.include_router(projects_router)
_fastapi_app.include_router(stats_router)
_fastapi_app.include_router(notifications_router)
_fastapi_app.include_router(audit_router)
_fastapi_app.include_router(schedules_router)
_fastapi_app.include_router(settings_router)
_fastapi_app.include_router(ai_assistant_router)
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
_fastapi_app.include_router(mtbf_router)
_fastapi_app.include_router(test_suites_router)


@_fastapi_app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "2.0.0"}


@_fastapi_app.get("/health/live")
async def health_live():
    """R01-F05（#885）：liveness 探针——进程在即可用，不做依赖检查。

    编排层若需「进程活着但不重启依赖抖动」的存活判断，指向本端点；
    Docker HEALTHCHECK 保持指向 /health（readiness）。
    """
    return {"data": {"status": "alive"}, "error": None}


@_fastapi_app.get("/health")
async def health_check():
    """readiness 探针（R01-F05，#885）：关键依赖不可用时非 200。

    此前 SAQ worker 退出 / Redis 断连时仍 200（saq_ready 只进 payload 不影响
    状态），Docker HEALTHCHECK 据此误报健康。现语义：
    - DB 断开 → 503 DB_UNAVAILABLE（既有）；
    - Redis 不可达 → 503 REDIS_UNREACHABLE（lifespan 已建 redis_client 时
      ping 验证；TESTING=1 下 redis_client 为 None，跳过）；
    - SAQ 未就绪 → 503 SAQ_NOT_READY（inprocess 与 producer 模式同判——
      ADR-0026 P0 pump 就绪 = worker 活跃或 producer 已连）；
    - ``STP_SKIP_INFRA_CHECK=1``（非生产类环境）与 lifespan 同条件跳过
      Redis/SAQ 检查（运维显式豁免的基础设施面）。
    """
    inprocess_saq = os.getenv("STP_ENABLE_INPROCESS_SAQ", "1") == "1"
    # TESTING=1 下 lifespan 不启动 Redis/SAQ（redis_client 为 None、SAQ 未起），
    # readiness 的基础设施检查随之跳过——与 skip_infra 运维豁免同通道。
    skip_infra = (
        os.getenv("TESTING") == "1"
        or (
            os.getenv("STP_SKIP_INFRA_CHECK", "0") == "1"
            and not is_production_like_env()
        )
    )
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

        if not skip_infra:
            if redis_client is not None:
                try:
                    await asyncio.wait_for(
                        redis_client.ping(),
                        timeout=_HEALTH_REDIS_PING_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "health_redis_ping_timeout after %.1fs",
                        _HEALTH_REDIS_PING_TIMEOUT,
                    )
                    return JSONResponse(
                        status_code=503,
                        content={"data": None, "error": {"code": "REDIS_UNREACHABLE", "message": "redis ping timed out"}},
                    )
                except Exception as exc:
                    logger.warning("health_redis_unreachable — %s", exc)
                    return JSONResponse(
                        status_code=503,
                        content={"data": None, "error": {"code": "REDIS_UNREACHABLE", "message": "redis disconnected"}},
                    )
            if not is_saq_ready():
                logger.warning("health_saq_not_ready inprocess=%s", inprocess_saq)
                return JSONResponse(
                    status_code=503,
                    content={"data": None, "error": {"code": "SAQ_NOT_READY", "message": "saq worker/producer not ready"}},
                )

        from backend.core.admission_queue import (
            admission_queue_enabled,
            admission_queue_flag_enabled,
            is_queue_pump_ready,
        )
        from backend.realtime.agent_sid_registry import agent_sid_registry_enabled
        from backend.realtime.socketio_redis import socketio_redis_adapter_enabled

        # 注意：以下两项是 **opt-in 配置开关**（ADR-0027 多实例能力，单实例
        # 默认 false），不是实时连接状态——恒 false ≠ redis/agent 异常。
        # 键名带 _enabled 后缀以消除歧义（曾误读为「适配器未连接」）。
        payload: dict = {
            "status": "healthy",
            "saq_ready": is_saq_ready(),
            "saq_inprocess_worker": inprocess_saq,
            "socketio_redis_adapter_enabled": socketio_redis_adapter_enabled(),
            "agent_sid_registry_enabled": agent_sid_registry_enabled(),
            "admission_queue_flag": admission_queue_flag_enabled(),
            "admission_queue_pump_ready": is_queue_pump_ready(),
            "admission_queue_enabled": admission_queue_enabled(),
        }
        return {"data": payload, "error": None}
    except Exception:
        return JSONResponse(status_code=503, content={"data": None, "error": {"code": "DB_UNAVAILABLE", "message": "database disconnected"}})
