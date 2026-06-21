"""Agent 运行日志 HTTP 下载端点（ADR-0025 方案 C 方案 A）。

控制平面通过此端点按需下载 Agent SSD 上的运行日志文件。
Agent 离线时运行日志不可用——合理约束。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

logger = logging.getLogger(__name__)

_BASE_LOG_DIR: Path | None = None


def _set_base_log_dir(log_dir: str | Path) -> None:
    global _BASE_LOG_DIR
    _BASE_LOG_DIR = Path(log_dir)


async def list_run_logs(request: Request) -> Response:
    job_id = request.path_params["job_id"]
    if _BASE_LOG_DIR is None:
        return JSONResponse({"error": "not configured"}, status_code=503)
    try:
        int(job_id)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid job_id"}, status_code=400)
    log_dir = _BASE_LOG_DIR / str(job_id)
    log_dir_resolved = log_dir.resolve()
    base_resolved = _BASE_LOG_DIR.resolve()
    if not str(log_dir_resolved).startswith(str(base_resolved)):
        return JSONResponse({"error": "path traversal denied"}, status_code=403)
    if not log_dir_resolved.is_dir():
        return JSONResponse({"error": "directory not found"}, status_code=404)
    files = sorted(f.name for f in log_dir_resolved.iterdir() if f.is_file())
    return JSONResponse({"job_id": int(job_id), "files": files})


async def download_run_log(request: Request) -> Response:
    job_id = request.path_params["job_id"]
    filename = request.path_params["filename"]
    if _BASE_LOG_DIR is None:
        return JSONResponse({"error": "not configured"}, status_code=503)
    try:
        int(job_id)
    except (ValueError, TypeError):
        return JSONResponse({"error": "invalid job_id"}, status_code=400)
    log_dir = _BASE_LOG_DIR / str(job_id)
    filepath = (log_dir / filename).resolve()
    base_resolved = _BASE_LOG_DIR.resolve()
    if not str(filepath).startswith(str(base_resolved)):
        return JSONResponse({"error": "path traversal denied"}, status_code=403)
    if not filepath.is_file():
        return JSONResponse({"error": "file not found"}, status_code=404)
    return FileResponse(str(filepath), filename=filepath.name)


def create_app(log_dir: str | Path) -> Starlette:
    _set_base_log_dir(log_dir)
    app = Starlette(
        routes=[
            Route("/run-logs/{job_id}", list_run_logs),
            Route("/run-logs/{job_id}/{filename}", download_run_log),
        ],
    )
    return app


def start_run_log_server(log_dir: str | Path, port: int = 8900) -> None:
    import threading
    import uvicorn

    _set_base_log_dir(log_dir)
    app = Starlette(
        routes=[
            Route("/run-logs/{job_id}", list_run_logs),
            Route("/run-logs/{job_id}/{filename}", download_run_log),
        ],
    )

    def _serve():
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")

    t = threading.Thread(target=_serve, name="run-log-server", daemon=True)
    t.start()
    logger.info("run_log_server_started port=%d log_dir=%s", port, log_dir)
