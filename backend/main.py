from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import heartbeat_router, hosts_router, tasks_router
from .api.routes.devices import router as devices_router
from .api.routes.websocket import router as websocket_router
from .core.database import Base, engine
from .scheduler.recycler import start_recycler
from .scheduler.dispatcher import start_dispatcher

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Stability Test Platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(heartbeat_router)
app.include_router(hosts_router)
app.include_router(tasks_router)
app.include_router(devices_router)
app.include_router(websocket_router)


@app.on_event("startup")
def _startup_background():
    # 启动回收器与调度器
    start_recycler()
    start_dispatcher()


@app.get("/")
def root():
    return {"message": "Stability Test Platform API", "version": "1.0.0"}
