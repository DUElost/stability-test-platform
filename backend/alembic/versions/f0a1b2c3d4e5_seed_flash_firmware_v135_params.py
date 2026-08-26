"""seed flash_firmware v1.3.5 params and deactivate v1.3.3

Revision ID: c0d1e2f3a4b5
Revises: b8c9d0e1f2a3
Create Date: 2026-08-26

Data migration (verify 预算上调):

1. Ensure flash_firmware v1.3.5 exists in the script table with populated
   param_schema and default_params — self-contained and deployment-order
   independent w.r.t. scan_script_root (e1f2a3b4c5d6 precedent).
2. Deactivate v1.3.3 (two generations back). v1.3.4 stays active as the
   rollback path.

Behavioral delta vs v1.3.4: verify_wait_seconds default 180 -> 300
(schema display default + script constant; the key stays out of
default_params as before). Evidence (.87 hub tree, 2026-08-26, three
flashes): post-flash boot-to-adb consistently exceeded 180s there (046
returned only after budget exhaustion; 166/193 parked on OOBE needing
manual steps), while .66 never exceeded ~105s. Raising the budget turns
"flash succeeded but judged failed" into "wait longer, get the true
verdict"; fast hosts are unaffected.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "f0a1b2c3d4e5"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

# ── flash_firmware v1.3.5 metadata ──────────────────────────────────────────

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
        "description": (
            "刷后等设备回到 adb 的上限；v1.3.5 起上调——"
            ".87 类 hub 树 host 的启动回归普遍超过 180s"
        ),
        "default": 300,
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
        "label": "重启方式",
        "description": (
            "normal=普通 adb reboot（完整上电流经 BROM 窗口，工具可抓中，默认）；"
            "bootloader/fastboot=热重启直达对应模式（跳过 BROM，SPFT 抓不到）"
        ),
        "enum": ["normal", "bootloader", "fastboot"],
        "default": "normal",
    },
    "pre_reboot_wait_seconds": {
        "type": "integer",
        "required": False,
        "label": "重启提前量(秒)",
        "description": (
            "工具进入 USB 扫描后、发 adb reboot 前的等待；"
            "BROM 窗口观测者必须就位在先（v1.3.3）"
        ),
        "default": 5,
        "minimum": 0,
    },
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
    "reboot_target": "normal",
    "pre_reboot_wait_seconds": 5,
}

# sha256 of flash_firmware.py v1.3.5
_CONTENT_SHA256 = "2a065aa2a51ea6ea781668d44c4b02dea11fa83172bbf0b6f4622c7a5765cedb"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # ── 1. Ensure v1.3.5 row exists with metadata ────────────────────────
    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.3.5"},
    ).fetchone()

    if row is None:
        prior = conn.execute(
            text(
                "SELECT nfs_path FROM script "
                "WHERE name = 'flash_firmware' AND version = '1.3.4'"
            ),
        ).fetchone()
        nfs_path = (
            prior.nfs_path.replace("/v1.3.4/", "/v1.3.5/") if prior
            else "flash_firmware/v1.3.5/flash_firmware.py"
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
                "ver": "1.3.5",
                "nfs": nfs_path,
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — v1.3.4 + verify budget 300s "
                    "(slow hub-tree hosts now judge correctly)"
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
                "ver": "1.3.5",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    # ── 2. Deactivate two-generations-back version (v1.3.1 stays: rollback) ─
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version IN ('1.0.0', '1.0.1', "
            "'1.1.0', '1.2.0', '1.3.0', '1.3.1', '1.3.2', '1.3.3')"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    # Undo: clear v1.3.5 metadata and reactivate v1.3.3.
    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.5'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.3'"
        ),
        {"now": now},
    )
