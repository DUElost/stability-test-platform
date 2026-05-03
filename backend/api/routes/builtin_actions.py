"""Builtin actions catalog API."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Literal, Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.api.response import ApiResponse, ok

router = APIRouter(prefix="/api/v1/builtin-actions", tags=["builtin-actions"])

_CATALOG_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "builtin_actions.json"


def _allowed_builtin_names() -> Set[str]:
    from backend.agent.actions import ACTION_REGISTRY

    return set(ACTION_REGISTRY.keys())


class BuiltinActionOut(BaseModel):
    name: str
    label: str
    category: Literal["device", "process", "file", "log", "script"]
    description: str = ""
    param_schema: Dict[str, dict] = Field(default_factory=dict)
    is_active: bool = True
    updated_at: str


class BuiltinActionUpdate(BaseModel):
    label: Optional[str] = None
    category: Optional[Literal["device", "process", "file", "log", "script"]] = None
    description: Optional[str] = None
    param_schema: Optional[Dict[str, dict]] = None
    is_active: Optional[bool] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat() + "Z"


def _load_catalog_raw() -> List[dict]:
    if not _CATALOG_PATH.exists():
        return []
    data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail="builtin_actions.json must be an array")
    return data


def _save_catalog_raw(items: List[dict]) -> None:
    _CATALOG_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _normalized_catalog() -> List[dict]:
    allowed = _allowed_builtin_names()
    items = _load_catalog_raw()
    by_name: Dict[str, dict] = {}
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name or name not in allowed:
            continue
        if name in by_name:
            continue
        by_name[name] = {
            "name": name,
            "label": str(item.get("label") or name),
            "category": item.get("category") or "script",
            "description": str(item.get("description") or ""),
            "param_schema": item.get("param_schema") if isinstance(item.get("param_schema"), dict) else {},
            "is_active": bool(item.get("is_active", True)),
            "updated_at": item.get("updated_at") or _now_iso(),
        }

    # 兜底补齐：确保所有可执行 builtin 至少有一条配置
    for name in sorted(allowed):
        if name in by_name:
            continue
        by_name[name] = {
            "name": name,
            "label": name,
            "category": "script",
            "description": "",
            "param_schema": {},
            "is_active": True,
            "updated_at": _now_iso(),
        }
    return sorted(by_name.values(), key=lambda x: x["name"])


@router.get("", response_model=ApiResponse[List[BuiltinActionOut]])
def list_builtin_actions(is_active: Optional[bool] = None):
    rows = _normalized_catalog()
    if is_active is not None:
        rows = [x for x in rows if x.get("is_active", True) is is_active]
    return ok([BuiltinActionOut(**x) for x in rows])


@router.get("/{name}", response_model=ApiResponse[BuiltinActionOut])
def get_builtin_action(name: str):
    rows = _normalized_catalog()
    for row in rows:
        if row["name"] == name:
            return ok(BuiltinActionOut(**row))
    raise HTTPException(status_code=404, detail=f"builtin action not found: {name}")


@router.put("/{name}", response_model=ApiResponse[BuiltinActionOut])
def update_builtin_action(name: str, payload: BuiltinActionUpdate):
    allowed = _allowed_builtin_names()
    if name not in allowed:
        raise HTTPException(status_code=422, detail=f"unknown builtin action: {name}")

    fields_set = payload.model_fields_set if hasattr(payload, "model_fields_set") else payload.__fields_set__
    rows = _normalized_catalog()
    target = None
    for row in rows:
        if row["name"] == name:
            target = row
            break
    if target is None:
        target = {
            "name": name,
            "label": name,
            "category": "script",
            "description": "",
            "param_schema": {},
            "is_active": True,
            "updated_at": _now_iso(),
        }
        rows.append(target)

    if "label" in fields_set and payload.label is not None:
        target["label"] = payload.label
    if "category" in fields_set and payload.category is not None:
        target["category"] = payload.category
    if "description" in fields_set:
        target["description"] = payload.description or ""
    if "param_schema" in fields_set:
        target["param_schema"] = payload.param_schema or {}
    if "is_active" in fields_set and payload.is_active is not None:
        target["is_active"] = payload.is_active
    target["updated_at"] = _now_iso()

    _save_catalog_raw(sorted(rows, key=lambda x: x["name"]))
    return ok(BuiltinActionOut(**target))
