"""seed flash_firmware v1.3.6 params and deactivate v1.3.4

Revision ID: i5j6k7l8m9n0
Revises: k8l9m0n1o2p3
Create Date: 2026-08-28

Data migration (per-model 版本映射):

1. Ensure flash_firmware v1.3.6 exists in the script table with populated
   param_schema and default_params (h2i3j4k5l6m7 precedent).
2. Deactivate v1.3.4 (two generations back). v1.3.5 stays active as the
   rollback path.

Behavioral delta vs v1.3.5: family pointer latest.json now supports a
per-model versions map ({"versions": {"MLD_LX2": "...", "MLD_LX3": "..."}});
the legacy single {"version": "..."} key still works. Enables LX2/LX3 to
route to their own firmware simultaneously — previously a version bump
required pointer swaps and staggered dispatch (2026-08-28 LX2 V62 batch).

Params identical to v1.3.5. nfs_path stays absolute per platform
convention.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "i5j6k7l8m9n0"
down_revision = "k8l9m0n1o2p3"
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

# sha256 of flash_firmware.py v1.3.6
_CONTENT_SHA256 = "1048549cf4b20f225ee969e4e84bfd199480c387b5160c8f6db25dd91759aacb"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_firmware", "ver": "1.3.6"},
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
                "ver": "1.3.6",
                "nfs": ("/opt/stability-test-agent/agent/scripts/"
                        "flash_firmware/v1.3.6/flash_firmware.py"),
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "MTK SP Flash Tool firmware flash — v1.3.5 + per-model "
                    "version map in the family pointer (LX2/LX3 parallel)"
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
                "ver": "1.3.6",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version IN ('1.0.0','1.0.1',"
            "'1.1.0','1.2.0','1.3.0','1.3.1','1.3.2','1.3.3','1.3.4')"
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
            "WHERE name = 'flash_firmware' AND version = '1.3.6'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_firmware' AND version = '1.3.4'"
        ),
        {"now": now},
    )
