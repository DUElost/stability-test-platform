"""seed flash_firmware v1.3.0 params and deactivate v1.1.0

Revision ID: a7b8c9d0e1f2
Revises: w9x0y1z2a3b4
Create Date: 2026-08-25

Data migration (MTK 多设备刷机消歧 · 门控 / 重试环 / 环境预检):

1. Ensure flash_firmware v1.3.0 exists in the script table with populated
   param_schema and default_params — self-contained and deployment-order
   independent w.r.t. scan_script_root (u7v8w9x0y1z2 precedent).
2. Deactivate v1.1.0 (superseded). v1.2.0 stays active as the rollback path,
   mirroring how u7v8w9x0y1z2 kept v1.1.0 around when seeding v1.2.0.

default_params intentionally contains only the v1.2.0-era "fixed desired"
keys (command/boot_mode/timeout/reboot_*). All four v1.3.0 knobs
(gate_other_mtk/max_attempts/retry_backoff_seconds/strict_env_check) have
STP_FLASH_* env escape hatches; seeding them would let params shadow the
env and kill fleet-level hot-update control (same rule that kept
skip_if_current / verify_version out of v1.2.0 defaults).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "a7b8c9d0e1f2"
down_revision = "w9x0y1z2a3b4"
branch_labels = None
depends_on = None

# ── flash_firmware v1.3.0 metadata ──────────────────────────────────────────

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
        "description": "单次尝试的 flash_tool 上限；重试整体上限约为其 × max_attempts",
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
    # ── v1.3.0：门控 / 重试 / 预检 ───────────────────────────────────────
    "gate_other_mtk": {
        "type": "boolean",
        "required": False,
        "label": "门控其它 MTK 口",
        "description": (
            "隐藏同 host 其它处于刷机态的 MTK 口（authorized=0），刷完恢复；"
            "只动非目标口，普通态(pid=2046)手机不受影响。默认 true。"
            "env STP_FLASH_GATE_OTHER_MTK"
        ),
        "default": True,
    },
    "max_attempts": {
        "type": "integer",
        "required": False,
        "label": "尝试次数",
        "description": (
            "整链路尝试次数（每次 = 重启 + 启动 flash_tool + 等结果）。"
            "默认 2，上限 4。env STP_FLASH_MAX_ATTEMPTS"
        ),
        "default": 2,
        "minimum": 1,
        "maximum": 4,
    },
    "retry_backoff_seconds": {
        "type": "integer",
        "required": False,
        "label": "重试间隔(秒)",
        "description": "相邻两次尝试之间的间隔；默认 10。env STP_FLASH_RETRY_BACKOFF",
        "default": 10,
        "minimum": 0,
    },
    "strict_env_check": {
        "type": "boolean",
        "required": False,
        "label": "严格环境预检",
        "description": (
            "true 时把 ttyACM 写入路径不明等 WARNING 升级为失败；默认 false。"
            "env STP_FLASH_STRICT_ENV_CHECK"
        ),
        "default": False,
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

# sha256 of flash_firmware.py v1.3.0
_CONTENT_SHA256 = "2eb055de1eee56de03e91fc9f41ae84248b13a9eabfafc42bf88b2357b025d0a"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # ── 1. Ensure v1.3.0 row exists with metadata ────────────────────────
    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.3.0"},
    ).fetchone()

    if row is None:
        prior = conn.execute(
            text(
                "SELECT nfs_path FROM script "
                "WHERE name = 'flash_firmware' AND version = '1.2.0'"
            ),
        ).fetchone()
        nfs_path = (
            prior.nfs_path.replace("/v1.2.0/", "/v1.3.0/") if prior
            else "flash_firmware/v1.3.0/flash_firmware.py"
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
                "ver": "1.3.0",
                "nfs": nfs_path,
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — multi-device gating, "
                    "retry loop, environment precheck (v1.2.0 routing kept)"
                ),
                "now": now,
            },
        )
    else:
        conn.execute(
            text(
                "UPDATE script SET default_params = CAST(:dparams AS jsonb), "
                " param_schema = CAST(:pschema AS jsonb), "
                " is_active = true, updated_at = :now "
                "WHERE name = :name AND version = :ver"
            ),
            {
                "name": "flash_firmware",
                "ver": "1.3.0",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    # ── 2. Deactivate superseded versions (v1.2.0 stays active: rollback) ─
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version IN ('1.0.0', '1.0.1', "
            "'1.1.0')"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Undo: clear v1.3.0 metadata and reactivate v1.1.0 (v1.2.0 was already
    # active before this revision and remains the primary rollback path).
    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.0'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.1.0'"
        ),
        {"now": now},
    )
