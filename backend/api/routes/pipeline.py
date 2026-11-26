"""Pipeline templates API: serves built-in pipeline templates from JSON files."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.api.routes.auth import get_current_active_user, User
from backend.agent.contracts.legacy_aee import LEGACY_AEE_TEMPLATE_NAMES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])

# Path to pipeline template JSON files
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "schemas" / "pipeline_templates"

# Seeded specialty keys in DB (migrations e6f7g8h9i0j1 / i0j1k2l3m4n5 / j1k2l3m4n5o6 / k2l3m4n5o6p7).
KNOWN_SPECIALTY_KEYS = frozenset({
    "mtbf",
    "power-cycle",
    "monkey",
    "sleep",
    "gpu",
    "ops",
})

_ALLOWED_PLAN_DEFAULT_KEYS = frozenset({
    "barrier_timeout_seconds",
    "barrier_max_wait_seconds",
    "auto_archive_interval_seconds",
    "watcher_policy",
})


class PipelineTemplateOut(BaseModel):
    name: str
    display_name: str
    specialty_key: str
    status: Literal["ready", "skeleton"] = "ready"
    requires_suite: bool = False
    description: str
    pipeline_def: Dict[str, Any]
    plan_defaults: Optional[Dict[str, Any]] = None


def _extract_pipeline_def(data: dict) -> dict:
    """Return normalized pipeline_def payload from template JSON."""
    lifecycle = data.get("lifecycle")
    if isinstance(lifecycle, dict):
        return {"lifecycle": lifecycle}
    return data


def _extract_plan_defaults(data: dict) -> Optional[Dict[str, Any]]:
    """Return whitelisted optional Plan-level defaults if declared on the template."""
    raw = data.get("plan_defaults")
    if not isinstance(raw, dict):
        return None
    filtered = {k: v for k, v in raw.items() if k in _ALLOWED_PLAN_DEFAULT_KEYS}
    return filtered or None


_TEMPLATE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _is_safe_template_name(name: str) -> bool:
    """Return True only for plain, single-component template names."""
    return bool(_TEMPLATE_NAME_RE.fullmatch(name or ""))


def _load_template(path: Path) -> PipelineTemplateOut:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw_status = data.get("status")
    status: Literal["ready", "skeleton"] = "skeleton" if raw_status == "skeleton" else "ready"
    raw_specialty = str(data.get("specialty_key") or "").strip()
    specialty_key = raw_specialty if raw_specialty else "ops"
    display_name = str(data.get("display_name") or "").strip() or path.stem
    return PipelineTemplateOut(
        name=path.stem,
        display_name=display_name,
        specialty_key=specialty_key,
        status=status,
        requires_suite=bool(data.get("requires_suite", False)),
        description=data.get("description", ""),
        pipeline_def=_extract_pipeline_def(data),
        plan_defaults=_extract_plan_defaults(data),
    )


def _is_hidden_template_name(name: str) -> bool:
    return name in LEGACY_AEE_TEMPLATE_NAMES


@router.get("/templates", response_model=List[PipelineTemplateOut])
def list_pipeline_templates(
    specialty_key: Annotated[Optional[str], Query(description="Optional specialty key filter")] = None,
    _current_user: User = Depends(get_current_active_user),
):
    """List all built-in pipeline templates (optionally filtered by specialty_key)."""
    if not TEMPLATES_DIR.exists():
        return []
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        if _is_hidden_template_name(f.stem):
            continue
        try:
            tpl = _load_template(f)
            if specialty_key and tpl.specialty_key != specialty_key:
                continue
            templates.append(tpl)
        except Exception:
            logger.warning("Failed to load pipeline template: %s", f.name)
    return templates


@router.get("/templates/{name}", response_model=PipelineTemplateOut)
def get_pipeline_template(
    name: str,
    _current_user: User = Depends(get_current_active_user),
):
    """Get a specific pipeline template by name."""
    if _is_hidden_template_name(name) or not _is_safe_template_name(name):
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    path = TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    return _load_template(path)
