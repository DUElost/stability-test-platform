from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

from backend.api.routes import auth_router, heartbeat_router, hosts_router, tasks_router
from backend.api.routes.devices import router as devices_router
from backend.api.routes.websocket import router as websocket_router
from backend.api.routes.metrics import router as metrics_router
from backend.api.routes.deploy import router as deploy_router
from backend.api.routes.users import router as users_router
from backend.core.database import Base, engine
from backend.core.limiter import RateLimitMiddleware
from backend.core.metrics import init_build_info
from backend.scheduler.recycler import start_recycler
from backend.scheduler.dispatcher import start_dispatcher

# Skip automatic table creation in testing mode (tests manage their own tables)
if os.getenv("TESTING") != "1":
    Base.metadata.create_all(bind=engine)

app = FastAPI(title="Stability Test Platform")
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


@app.on_event("startup")
def _startup_background():
    # Skip startup background tasks in testing mode
    if os.getenv("TESTING") == "1":
        return
    # 启动回收器与调度器
    start_recycler()
    start_dispatcher()
    # 初始化构建信息
    init_build_info(version="1.0.0", commit="unknown")


@app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "1.0.0"}
