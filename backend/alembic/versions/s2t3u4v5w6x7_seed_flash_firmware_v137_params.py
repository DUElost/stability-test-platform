"""seed flash_firmware v1.3.7 params and deactivate v1.3.5

Revision ID: s2t3u4v5w6x7
Revises: r0s9t8u7v6w5
Create Date: 2026-08-30

Data migration (锁内 boot 稳定等待):

1. Ensure flash_firmware v1.3.7 exists in the script table with populated
   param_schema and default_params (i5j6k7l8m9n0 precedent).
2. Deactivate v1.3.5 (two generations back). v1.3.6 stays active as the
   rollback path.

Behavioral delta vs v1.3.6: after post-flash verify passes, keep holding
the host serial lock until sys.boot_completed=1 AND the USB topology
fingerprint is stable for boot_stabilize_seconds (default 20) — the
first-boot second reboot window (HONOR 首刷后初始化重启) happens inside
the lock, so the next job's flash_tool scan cannot grab the previous
device's BROM/preloader state and write the new firmware into it
(2026-08-30 run 258: 11/15 台 "Download Succeeded 但目标未变").

New params (both optional with defaults):
- boot_stabilize_seconds  (default 20)
- boot_stabilize_max_wait (default 120; timeout releases the lock per the
  v1.3.4 "confirmed stuck" semantics — does not fail the job)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "s2t3u4v5w6x7"
down_revision = "f6g7h8i9j0k1"
branch_labels = None
depends_on = None

PARAM_SCHEMA = {
    "firmware_dir": {
        "type": "string",
        "required": False,
        "label": "固件目录",
        "description": "NFS 相对或绝对路径。缺省走指纹路由",
    },
    "da_file": {"type": "string", "required": False, "label": "DA 文件"},
    "scatter_file": {"type": "string", "required": False, "label": "Scatter 文件"},
    "family": {
        "type": "string", "required": False, "label": "机型族",
        "enum": ["MLD", "ELA"],
    },
    "version": {
        "type": "string", "required": False, "label": "目标固件版本",
        "description": "缺省按机型读 latest.json 的 versions 映射或单键 version",
    },
    "firmware_root": {
        "type": "string", "required": False, "label": "固件根目录",
    },
    "skip_if_current": {
        "type": "boolean", "required": False, "label": "同版本跳过",
        "default": True,
    },
    "verify_version": {
        "type": "boolean", "required": False, "label": "刷后版本核验",
        "default": True,
    },
    "verify_wait_seconds": {
        "type": "integer", "required": False, "label": "核验等待(秒)",
        "default": 300, "minimum": 30,
    },
    "boot_stabilize_seconds": {
        "type": "integer", "required": False, "label": "boot 稳定窗口(秒)",
        "default": 20, "minimum": 5,
        "description": "verify 通过后 boot_completed=1 且 USB 拓扑稳定的持续"
                       "窗口——首刷二次重启在锁内消化",
    },
    "boot_stabilize_max_wait": {
        "type": "integer", "required": False, "label": "boot 稳定等待上限(秒)",
        "default": 120, "minimum": 30,
        "description": "超时按「设备确认卡死」放行,不判失败",
    },
    "command": {
        "type": "string", "required": False, "label": "刷机命令",
        "enum": ["firmware-upgrade", "format-download", "readback",
                 "download-only"],
        "default": "firmware-upgrade",
    },
    "boot_mode": {
        "type": "string", "required": False, "label": "启动模式",
        "enum": ["auto", "da", "boot1"], "default": "auto",
    },
    "timeout_seconds": {
        "type": "integer", "required": False, "label": "超时(秒)",
        "default": 1200, "minimum": 60,
    },
    "flash_tool_dir": {
        "type": "string", "required": False, "label": "Flash Tool 目录",
    },
    "reboot_to_flash": {
        "type": "boolean", "required": False, "label": "刷前重启设备",
        "default": True,
    },
    "reboot_target": {
        "type": "string", "required": False, "label": "重启方式",
        "enum": ["normal", "bootloader", "fastboot"], "default": "normal",
    },
    "pre_reboot_wait_seconds": {
        "type": "integer", "required": False, "label": "重启提前量(秒)",
        "default": 5, "minimum": 0,
    },
    "gate_other_mtk": {
        "type": "boolean", "required": False, "label": "门控其它 MTK 口",
        "default": True,
    },
    "max_attempts": {
        "type": "integer", "required": False, "label": "尝试次数",
        "default": 2, "minimum": 1, "maximum": 4,
    },
    "retry_backoff_seconds": {
        "type": "integer", "required": False, "label": "重试间隔(秒)",
        "default": 10, "minimum": 0,
    },
    "strict_env_check": {
        "type": "boolean", "required": False, "label": "严格环境预检",
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

# sha256 of flash_firmware.py v1.3.7
_CONTENT_SHA256 = "7405b9ab3f7af46e7e0a4e6b25ab9bb55dd1df054a5af877dd84a9c9a023a0d9"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.3.7"},
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
                "name": "flash_firmware",
                "display": "flash_firmware",
                "cat": "device",
                "stype": "python",
                "ver": "1.3.7",
                "nfs": ("/opt/stability-test-agent/agent/scripts/"
                        "flash_firmware/v1.3.7/flash_firmware.py"),
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — v1.3.6 + 锁内 boot "
                    "稳定等待（首刷二次重启窗口在锁内消化）"
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
                "ver": "1.3.7",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version IN ('1.0.0','1.0.1',"
            "'1.1.0','1.2.0','1.3.0','1.3.1','1.3.2','1.3.3','1.3.4','1.3.5')"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.7'"
        ),
        {"now": now},
    )

    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.6'"
        ),
        {"now": now},
    )
