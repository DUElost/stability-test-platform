"""DEPRECATED: Script batch user-facing endpoints (Phase 5d).

All routes return 410 Gone. Use /api/v1/script-executions instead.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/v1/script-batches", tags=["script-batches"])

_DEPRECATION_MSG = (
    "This endpoint is deprecated. "
    "Script batch API has been replaced by /api/v1/script-executions."
)


async def _gone():
    return JSONResponse(status_code=410, content={"detail": _DEPRECATION_MSG})


router.add_api_route("/{path:path}", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
router.add_api_route("/", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
router.add_api_route("", _gone, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
