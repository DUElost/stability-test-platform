"""seed log_prepare@1.1.0 authoritative param_schema and default_params (ADR-0067 D2.6)

Revision ID: a67u2b3c4d5e6
Revises: a67u1c2d3e4f5
Create Date: 2026-11-28

Data migration:

1. Ensure ``log_prepare@1.1.0`` exists with authoritative, versioned
   ``param_schema`` (capacity bounds nailed down: ``mobilelog_size_mb`` in
   ``[256, 20480]``, ``ylog_size_mb`` in ``[64, 10240]``, nullable optional
   fields ``aee_mode``/``mtk_sublog_cmd``, mandatory ``android``/``kernel``
   streams) and ``default_params`` (``mobilelog_size_mb=4096``,
   ``mtk_auto_start=true``, ``ylog_size_mb=4096``, ``ylog_rotate=true``,
   ``ylog_required_streams=["android", "kernel"]``).
2. Does NOT modify or deactivate ``log_prepare@1.0.0`` (ADR-0051 / AGENTS.md
   immutability invariant; existing Plans and templates stay valid until U3).
3. Follows ``backend/services/script_seed_governance.py`` (#942 / #2055): embeds
   ``_raise_if_any_version_referenced`` before updating any pre-existing row
   or deactivating in ``downgrade()``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from alembic import op
from sqlalchemy import text

revision = "a67u2b3c4d5e6"
down_revision = "a67u1c2d3e4f5"
branch_labels = None
depends_on = None

SCRIPT_NAME = "log_prepare"
SCRIPT_VERSION = "1.1.0"
SCRIPT_ENTRY_SHA256 = (
    "96e59b4d719ec5047e59de194bc7366d4d5bc17a3c0c011c1096db004928de3d"
)
SCRIPT_DESC = (
    "统一设备持续日志前置准备 — v1.1.0 建立控制面可冻结 param_schema 与 default_params"
    "（ADR-0067 D2.6，脚本执行语义与 v1.0.0 一致）"
)

LOG_PREPARE_V110_PARAM_SCHEMA: dict[str, Any] = {
    "mobilelog_size_mb": {
        "type": "integer",
        "default": 4096,
        "minimum": 256,
        "maximum": 20480,
        "label": "MTK MobileLog 容量上限 (MB)",
        "description": "MTK 持续日志总容量上限（256–20480 MB）；下限 256 对齐脚本钳制",
    },
    "mtk_auto_start": {
        "type": "boolean",
        "default": True,
        "label": "MTK 开机自启持续日志",
        "description": "是否在 MTK 设备重启后保持 MobileLog 持续采集",
    },
    "mtk_sublog_cmd": {
        "type": "string",
        "nullable": True,
        "pattern": "^[A-Za-z0-9_.-]{1,64}$",
        "label": "MTK 子日志开关指令",
        "description": "可选 DebugLoggerUI 子日志指令（如 set_sublog_4_5_0）；置空则保留设备当前配置",
    },
    "aee_mode": {
        "type": "string",
        "nullable": True,
        "pattern": "^[0-9]{1,2}$",
        "label": "MTK AEE 模式",
        "description": "可选 persist.vendor.mtk.aee.mode 目标值；置空则保留设备当前值",
    },
    "ylog_size_mb": {
        "type": "integer",
        "default": 4096,
        "minimum": 64,
        "maximum": 10240,
        "label": "展锐 YLog 容量上限 (MB)",
        "description": "展锐 ylogctl aplogmaxsize 容量上限（64–10240 MB）",
    },
    "ylog_rotate": {
        "type": "boolean",
        "default": True,
        "label": "展锐 YLog 循环覆盖",
        "description": "是否启用展锐 ylogctl rotatelog 循环覆盖",
    },
    "ylog_required_streams": {
        "type": "array",
        "default": ["android", "kernel"],
        "minItems": 1,
        "items": {
            "type": "string",
            "enum": ["android", "kernel", "uboot", "lastlog"],
        },
        "required_items": ["android", "kernel"],
        "label": "展锐 YLog 必需子流",
        "description": "展锐 ylogctl query 必须处于 Open 态的子流清单，必须包含 android 与 kernel",
    },
}

LOG_PREPARE_V110_DEFAULT_PARAMS: dict[str, Any] = {
    "mobilelog_size_mb": 4096,
    "mtk_auto_start": True,
    "ylog_size_mb": 4096,
    "ylog_rotate": True,
    "ylog_required_streams": ["android", "kernel"],
}


def _count_plan_step_references(
    conn: Any, *, script_name: str, script_version: str
) -> int:
    """plan_step 中引用该 (script_name, script_version) 的步骤数。"""
    return int(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM plan_step "
                "WHERE script_name = :name AND script_version = :ver"
            ),
            {"name": script_name, "ver": script_version},
        ).scalar_one()
    )


def _raise_if_any_version_referenced(
    conn: Any, *, script_name: str, versions: list[str]
) -> None:
    """内嵌自 backend/services/script_seed_governance.py（#942/#2055 裁决：迁移自包含）。"""
    blocked: list[tuple[str, int]] = []
    for ver in versions:
        n = _count_plan_step_references(
            conn, script_name=script_name, script_version=ver
        )
        if n > 0:
            blocked.append((ver, n))
    if blocked:
        detail = ", ".join(f"{script_name} {v} ×{n}" for v, n in blocked)
        raise RuntimeError(
            f"seed migration aborted: 待修改/停用版本仍被 plan_step 引用：{detail}。"
            "请重指 plan_step 到在用版本，或以新建版本表达参数变化后重跑迁移。"
        )


def _as_json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            loaded = json.loads(raw)
            return loaded if isinstance(loaded, dict) else {}
        except ValueError:
            return {}
    return {}


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    pschema_json = json.dumps(LOG_PREPARE_V110_PARAM_SCHEMA, ensure_ascii=False)
    dparams_json = json.dumps(LOG_PREPARE_V110_DEFAULT_PARAMS, ensure_ascii=False)

    row = conn.execute(
        text(
            "SELECT id, param_schema, default_params, is_active "
            "FROM script WHERE name = :name AND version = :ver"
        ),
        {"name": SCRIPT_NAME, "ver": SCRIPT_VERSION},
    ).fetchone()

    if row is None:
        conn.execute(
            text(
                "INSERT INTO script "
                "(name, display_name, category, script_type, version, nfs_path, "
                " content_sha256, param_schema, default_params, is_active, "
                " description, created_at, updated_at) "
                "VALUES (:name, :display, :cat, :stype, :ver, :nfs, "
                " :sha, CAST(:pschema AS jsonb), CAST(:dparams AS jsonb), true, "
                " :desc, :now, :now)"
            ),
            {
                "name": SCRIPT_NAME,
                "display": SCRIPT_NAME,
                "cat": "device",
                "stype": "python",
                "ver": SCRIPT_VERSION,
                "nfs": (
                    "/opt/stability-test-agent/agent/scripts/"
                    f"{SCRIPT_NAME}/v{SCRIPT_VERSION}/{SCRIPT_NAME}.py"
                ),
                "sha": SCRIPT_ENTRY_SHA256,
                "pschema": pschema_json,
                "dparams": dparams_json,
                "desc": SCRIPT_DESC,
                "now": now,
            },
        )
        return

    existing_schema = _as_json_obj(row[1])
    existing_defaults = _as_json_obj(row[2])
    existing_active = bool(row[3])
    if (
        existing_schema == LOG_PREPARE_V110_PARAM_SCHEMA
        and existing_defaults == LOG_PREPARE_V110_DEFAULT_PARAMS
        and existing_active
    ):
        return

    _raise_if_any_version_referenced(
        conn, script_name=SCRIPT_NAME, versions=[SCRIPT_VERSION]
    )
    conn.execute(
        text(
            "UPDATE script "
            "SET param_schema = CAST(:pschema AS jsonb), "
            "    default_params = CAST(:dparams AS jsonb), "
            "    is_active = true, "
            "    description = :desc, "
            "    updated_at = :now "
            "WHERE name = :name AND version = :ver"
        ),
        {
            "name": SCRIPT_NAME,
            "ver": SCRIPT_VERSION,
            "pschema": pschema_json,
            "dparams": dparams_json,
            "desc": SCRIPT_DESC,
            "now": now,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    _raise_if_any_version_referenced(
        conn, script_name=SCRIPT_NAME, versions=[SCRIPT_VERSION]
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = :name AND version = :ver"
        ),
        {"name": SCRIPT_NAME, "ver": SCRIPT_VERSION, "now": now},
    )
