import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# 加载 .env 文件（指定 backend 目录）
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from sqlalchemy import inspect, text

from backend.api.routes import auth_router, heartbeat_router, hosts_router, tasks_router
from backend.api.routes.devices import router as devices_router
from backend.api.routes.websocket import router as websocket_router, capture_main_loop
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.deploy import router as deploy_router
from backend.api.routes.users import router as users_router
from backend.api.routes.tools import router as tools_router
from backend.api.routes.results import router as results_router
from backend.api.routes.workflows import router as workflows_router
from backend.api.routes.stats import router as stats_router
from backend.api.routes.notifications import router as notifications_router
from backend.api.routes.audit import router as audit_router
from backend.api.routes.schedules import router as schedules_router
from backend.api.routes.templates import router as templates_router
from backend.core.database import Base, engine
from backend.core.limiter import RateLimitMiddleware
from backend.core.metrics import init_build_info
from backend.scheduler.recycler import start_recycler
from backend.scheduler.dispatcher import start_dispatcher
from backend.scheduler.workflow_executor import start_workflow_executor
from backend.scheduler.cron_scheduler import start_cron_scheduler

logger = logging.getLogger(__name__)

# Skip automatic table creation in testing mode (tests manage their own tables)
if os.getenv("TESTING") != "1":
    Base.metadata.create_all(bind=engine)
    # Migrate existing tables: add missing columns that create_all won't handle
    # 注意：PostgreSQL 使用 TIMESTAMP 而不是 DATETIME
    _MIGRATIONS = [
        # task_runs 新增字段
        ("task_runs", "report_json", "TEXT"),
        ("task_runs", "jira_draft_json", "TEXT"),
        ("task_runs", "post_processed_at", "TIMESTAMP"),
        ("task_runs", "group_id", "VARCHAR(32)"),
        ("task_runs", "progress", "INTEGER DEFAULT 0"),
        ("task_runs", "progress_message", "VARCHAR(256)"),
        ("task_runs", "last_heartbeat_at", "TIMESTAMP"),
        ("task_runs", "error_code", "VARCHAR(64)"),
        # tasks 新增字段
        ("tasks", "tool_id", "INTEGER"),
        ("tasks", "tool_snapshot", "TEXT"),
        ("tasks", "group_id", "VARCHAR(32)"),
        ("tasks", "is_distributed", "BOOLEAN DEFAULT FALSE"),
        ("tasks", "template_id", "INTEGER"),
        # task_templates 新增字段
        ("task_templates", "description", "VARCHAR(256)"),
        # workflows 新增字段
        ("workflows", "is_template", "BOOLEAN DEFAULT FALSE"),
    ]
    with engine.connect() as conn:
        insp = inspect(engine)
        for table, column, col_type in _MIGRATIONS:
            if table in insp.get_table_names():
                existing = {c["name"] for c in insp.get_columns(table)}
                if column not in existing:
                    try:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                        logger.info("migration_added_column: %s.%s", table, column)
                    except Exception as e:
                        logger.warning("migration_skip_column: %s.%s - %s", table, column, e)
        conn.commit()

app = FastAPI(title="Stability Test Platform")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
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
app.include_router(deploy_router)
app.include_router(users_router)
app.include_router(tools_router)
app.include_router(results_router)
app.include_router(workflows_router)
app.include_router(stats_router)
app.include_router(notifications_router)
app.include_router(audit_router)
app.include_router(schedules_router)
app.include_router(templates_router)


@app.on_event("startup")
def _startup_background():
    # Skip startup background tasks in testing mode
    if os.getenv("TESTING") == "1":
        return
    # 启动回收器与调度器
    start_recycler()
    start_dispatcher()
    start_workflow_executor()
    start_cron_scheduler()
    # 捕获主事件循环，供后台线程安全广播
    capture_main_loop()
    # 初始化构建信息
    init_build_info(version="1.0.0", commit="unknown")


@app.on_event("shutdown")
def _shutdown_pool():
    from backend.core.thread_pool import shutdown
    shutdown(wait=False)


@app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "1.0.0"}


@app.get("/health")
def health_check():
    """Health check endpoint for load balancers and monitoring."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        # 不返回具体异常信息，避免泄露内部实现细节
        logger.error(f"Health check failed: {type(e).__name__}")
        return JSONResponse(status_code=503, content={"status": "unhealthy", "database": "disconnected"})
