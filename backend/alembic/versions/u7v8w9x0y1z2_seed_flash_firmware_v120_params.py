"""seed flash_firmware v1.2.0 params and deactivate v1.0.0/v1.0.1

Revision ID: u7v8w9x0y1z2
Revises: t6u7v8w9x0y1
Create Date: 2026-08-22

Data migration (Honor 刷机自动化 · 方向 A):

1. Ensure flash_firmware v1.2.0 exists in the script table with populated
   param_schema and default_params — self-contained and deployment-order
   independent w.r.t. scan_script_root (b7c8d9e0f1a2 precedent).
2. Deactivate v1.0.0 and v1.0.1 (superseded; no plan references any flash
   version — verified 2026-08-22). v1.1.0 stays active as the rollback path.

v1.0.0 was already deactivated by b7c8d9e0f1a2, but scan_script_root used to
resurrect on-disk rows with is_active=false; that resurrection is fixed in the
same deploy, so this deactivation sticks.

default_params intentionally contains only the "fixed desired" keys
(command/boot_mode/timeout/reboot_*). skip_if_current / verify_version /
firmware_root / version are NOT defaulted here: params take precedence over
env in the script's param_or_env chain, and seeding them would kill the
hot-update env escape hatches (STP_FLASH_*).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "u7v8w9x0y1z2"
down_revision = "t6u7v8w9x0y1"
branch_labels = None
depends_on = None

# ── flash_firmware v1.2.0 metadata ──────────────────────────────────────────

PARAM_SCHEMA = {
    "firmware_dir": {
        "type": "string",
        "required": False,
        "label": "固件目录",
        "description": (
            "NFS 相对或绝对路径。缺省走指纹路由："
            "{STP_NFS_ROOT}/firmware/{family}/{version}/"
        ),
    },
    "da_file": {
        "type": "string",
        "required": False,
        "label": "DA 文件",
        "description": "相对 firmware_dir 或绝对路径；缺省由 manifest.json 提供",
    },
    "scatter_file": {
        "type": "string",
        "required": False,
        "label": "Scatter 文件",
        "description": "相对 firmware_dir 或绝对路径；缺省由 manifest.json 提供",
    },
    "family": {
        "type": "string",
        "required": False,
        "label": "机型族",
        "description": "显式指定（如 MLD）；缺省按 getprop ro.product.model 路由",
        "enum": ["MLD", "ELA"],
    },
    "version": {
        "type": "string",
        "required": False,
        "label": "目标固件版本",
        "description": (
            "缺省读 {root}/{family}/latest.json；env STP_FLASH_FIRMWARE_VERSION"
        ),
    },
    "firmware_root": {
        "type": "string",
        "required": False,
        "label": "固件根目录",
        "description": "缺省 {STP_NFS_ROOT}/firmware；env STP_FLASH_FIRMWARE_ROOT",
    },
    "skip_if_current": {
        "type": "boolean",
        "required": False,
        "label": "同版本跳过",
        "description": "刷前 getprop 比对，已是目标版本则 skipped；默认 true",
        "default": True,
    },
    "verify_version": {
        "type": "boolean",
        "required": False,
        "label": "刷后版本核验",
        "description": "回读版本与 manifest 不一致判失败；默认 true",
        "default": True,
    },
    "verify_wait_seconds": {
        "type": "integer",
        "required": False,
        "label": "核验等待(秒)",
        "description": "刷后等设备回到 adb 的上限",
        "default": 180,
        "minimum": 30,
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

# sha256 of flash_firmware.py v1.2.0
_CONTENT_SHA256 = "956086baed98a61945aba9bcaf11ba18773234ff586a1d4e6ae3575c9ce7d067"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # ── 1. Ensure v1.2.0 row exists with metadata ────────────────────────
    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.2.0"},
    ).fetchone()

    if row is None:
        prior = conn.execute(
            text(
                "SELECT nfs_path FROM script "
                "WHERE name = 'flash_firmware' AND version = '1.1.0'"
            ),
        ).fetchone()
        nfs_path = (
            prior.nfs_path.replace("/v1.1.0/", "/v1.2.0/") if prior
            else "flash_firmware/v1.2.0/flash_firmware.py"
        )

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
                "ver": "1.2.0",
                "nfs": nfs_path,
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — fingerprint routing + "
                    "manifest + pre/post version checks"
                ),
                "now": now,
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE script SET default_params = CAST(:dparams AS jsonb), "
                " param_schema = CAST(:pschema AS jsonb), updated_at = :now "
                "WHERE name = :name AND version = :ver"
            ),
            {
                "name": "flash_firmware",
                "ver": "1.2.0",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    # ── 2. Deactivate superseded versions (v1.1.0 stays active: rollback) ─
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version IN ('1.0.0', '1.0.1')"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Undo: clear v1.2.0 metadata and reactivate v1.0.1 (v1.0.0 stays
    # inactive — b7c8d9e0f1a2's deactivation predates this revision).
    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.2.0'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.0.1'"
        ),
        {"now": now},
    )
