"""DEPRECATED: Agent-facing script batch endpoints (Phase 5d).

All routes return 410 Gone. Use /api/v1/script-executions instead.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/agent/script-batches", tags=["agent-script-batches"])

_DEPRECATION_MSG = (
    "This endpoint is deprecated. "
    "Agent script batch endpoints have been removed in favor of unified script_execution pipeline."
)


async def _gone():
    return JSONResponse(status_code=410, content={"detail": _DEPRECATION_MSG})


router.add_api_route("/{path:path}", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
router.add_api_route("/", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
router.add_api_route("", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
