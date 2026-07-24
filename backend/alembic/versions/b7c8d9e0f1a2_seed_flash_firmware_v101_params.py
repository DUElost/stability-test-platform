"""seed flash_firmware v1.0.1 default_params and param_schema

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-01

Data migration: ensure flash_firmware v1.0.1 exists in the script table
with populated default_params and param_schema. If v1.0.1 was already
registered by a prior scan_script_root run, UPDATE it; otherwise INSERT
a complete row so this migration is self-contained and deployment-order
independent.

Also deactivate v1.0.0 (which has empty default_params) so new PlanSteps
default to v1.0.1.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

# ── flash_firmware v1.0.1 metadata ──────────────────────────────────────────

PARAM_SCHEMA = {
    "firmware_dir": {
        "type": "string",
        "required": True,
        "label": "固件目录",
        "description": "NFS 相对或绝对路径，含 scatter/DA 文件",
    },
    "da_file": {
        "type": "string",
        "required": True,
        "label": "DA 文件",
        "description": "相对 firmware_dir 或绝对路径",
    },
    "scatter_file": {
        "type": "string",
        "required": True,
        "label": "Scatter 文件",
        "description": "相对 firmware_dir 或绝对路径",
    },
    "command": {
        "type": "string",
        "required": False,
        "label": "刷机命令",
        "enum": [
            "firmware-upgrade",
            "format-download",
            "readback",
            "download-only",
        ],
        "default": "firmware-upgrade",
    },
    "boot_mode": {
        "type": "string",
        "required": False,
        "label": "启动模式",
        "enum": ["auto", "da", "boot1"],
        "default": "auto",
    },
    "timeout_seconds": {
        "type": "integer",
        "required": False,
        "label": "超时(秒)",
        "default": 1200,
        "minimum": 60,
    },
    "flash_tool_dir": {
        "type": "string",
        "required": False,
        "label": "Flash Tool 目录",
        "description": "覆盖 STP_FLASH_TOOL_DIR 环境变量",
    },
    "reboot_to_flash": {
        "type": "boolean",
        "required": False,
        "label": "刷前重启设备",
        "default": True,
    },
    "reboot_target": {
        "type": "string",
        "required": False,
        "label": "重启目标",
        "enum": ["bootloader", "fastboot"],
        "default": "bootloader",
    },
    "pre_reboot_wait_seconds": {
        "type": "integer",
        "required": False,
        "label": "重启等待(秒)",
        "default": 5,
        "minimum": 0,
    },
}

DEFAULT_PARAMS = {
    "command": "firmware-upgrade",
    "boot_mode": "auto",
    "timeout_seconds": 1200,
    "reboot_to_flash": True,
    "reboot_target": "bootloader",
    "pre_reboot_wait_seconds": 5,
}

# sha256 of flash_firmware.py (same content across v1.0.0 and v1.0.1)
_CONTENT_SHA256 = "a4065d51ae8b793fcf04e87c9e0832e7280652958f1dd5d1a4406c43b9aa1d84"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # ── 1. Ensure v1.0.1 row exists ───────────────────────────────────────
    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.0.1"},
    ).fetchone()

    if row is None:
        # Derive nfs_path from v1.0.0 if available, else use a sensible default
        v100 = conn.execute(
            text("SELECT nfs_path FROM script WHERE name = 'flash_firmware' AND version = '1.0.0'"),
        ).fetchone()
        nfs_path = (v100.nfs_path.replace("/v1.0.0/", "/v1.0.1/") if v100
                    else "flash_firmware/v1.0.1/flash_firmware.py")

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
                "name": "flash_firmware",
                "display": "flash_firmware",
                "cat": "device",
                "stype": "python",
                "ver": "1.0.1",
                "nfs": nfs_path,
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": "MTK platform firmware flash via SP Flash Tool — structured params",
                "now": now,
            },
        )
    else:
        # v1.0.1 already exists (scan ran first) — just backfill
        conn.execute(
            text(
                "UPDATE script SET default_params = CAST(:dparams AS jsonb), "
                " param_schema = CAST(:pschema AS jsonb), updated_at = :now "
                "WHERE name = :name AND version = :ver"
            ),
            {
                "name": "flash_firmware",
                "ver": "1.0.1",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    # ── 2. Backfill param_schema on v1.0.0 (read-only reference) ──────────
    conn.execute(
        text(
            "UPDATE script SET param_schema = CAST(:pschema AS jsonb), updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.0.0'"
        ),
        {"pschema": json.dumps(PARAM_SCHEMA), "now": now},
    )

    # ── 3. Deactivate v1.0.0 ──────────────────────────────────────────────
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.0.0'"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Undo: clear v1.0.1 metadata
    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.0.1'"
        ),
        {"now": now},
    )

    # Undo: restore v1.0.0 param_schema to empty and reactivate
    conn.execute(
        text(
            "UPDATE script SET param_schema = '{}'::jsonb, is_active = true, "
            " updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.0.0'"
        ),
        {"now": now},
    )
