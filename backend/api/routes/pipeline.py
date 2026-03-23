"""Pipeline templates API: serves built-in pipeline templates from JSON files."""

import json
import logging
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/pipeline", tags=["pipeline"])

# Path to pipeline template JSON files
TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "schemas" / "pipeline_templates"


class PipelineTemplateOut(BaseModel):
    name: str
    description: str
    pipeline_def: dict


def _extract_pipeline_def(data: dict) -> dict:
    """Return normalized pipeline_def payload from template JSON."""
    stages = data.get("stages")
    if isinstance(stages, dict):
        return {"stages": stages}
    lifecycle = data.get("lifecycle")
    if isinstance(lifecycle, dict):
        return {"lifecycle": lifecycle}
    return data


def _load_template(path: Path) -> PipelineTemplateOut:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PipelineTemplateOut(
        name=path.stem,
        description=data.get("description", ""),
        pipeline_def=_extract_pipeline_def(data),
    )


@router.get("/templates", response_model=List[PipelineTemplateOut])
def list_pipeline_templates():
    """List all built-in pipeline templates."""
    if not TEMPLATES_DIR.exists():
        return []
    templates = []
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            templates.append(_load_template(f))
        except Exception:
            logger.warning("Failed to load pipeline template: %s", f.name)
    return templates


@router.get("/templates/{name}", response_model=PipelineTemplateOut)
def get_pipeline_template(name: str):
    """Get a specific pipeline template by name."""
    path = TEMPLATES_DIR / f"{name}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    return _load_template(path)
