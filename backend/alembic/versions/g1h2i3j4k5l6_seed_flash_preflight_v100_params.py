"""seed flash_preflight v1.0.0 params

Revision ID: g1h2i3j4k5l6
Revises: f0a1b2c3d4e5
Create Date: 2026-08-27

Data migration (刷机 fleet 主机前置自动化 · ensure 型):

1. Ensure flash_preflight v1.0.0 exists in the script table with populated
   param_schema and default_params (f0a1b2c3d4e5 precedent).

编排语义：Plan 中固定排在 flash_firmware 之前的 host 级前置步骤——幂等
ensure（Qt 运行库 ×5 / flashtool 执行位 / dialout 组 / udev 0666 规则 /
sudo 可用性 / NFS 固件指针），缺则自动修复并全程 PROGRESS 留痕；同 host
并发由 /tmp/stp-flash-preflight.lock 去重。

nfs_path 按平台约定写**绝对路径**
（/opt/stability-test-agent/agent/scripts/...，与 scan 写入一致；
相对字面量会让派发期 script sync 报 cannot map nfs_path——oobe_skip
v1.0/v1.1 教训）。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "g1h2i3j4k5l6"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

PARAM_SCHEMA = {
    "fix": {
        "type": "boolean",
        "required": False,
        "label": "自动修复",
        "description": (
            "true=检查→缺则自动修复（默认）；false=只检不修，"
            "缺项如实判失败"
        ),
        "default": True,
    },
    "skip_apt": {
        "type": "boolean",
        "required": False,
        "label": "跳过包类检查",
        "description": (
            "无外网/apt 镜像的 host 置 true：包类只记 warning 不判失败。"
            "默认 false"
        ),
        "default": False,
    },
    "dialout_user": {
        "type": "string",
        "required": False,
        "label": "dialout 目标用户",
        "description": "缺省取运行本脚本的当前用户（agent 服务用户 android）",
        "default": "android",
    },
}

DEFAULT_PARAMS = {
    "fix": True,
    "skip_apt": False,
    "dialout_user": "android",
}

# sha256 of flash_preflight.py v1.0.0
_CONTENT_SHA256 = "f319db7d27cfdf295e328a31f5b355691977bf3987a8555bbcf95641f32d6b71"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_preflight", "ver": "1.0.0"},
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
                "name": "flash_preflight",
                "display": "flash_preflight",
                "cat": "device",
                "stype": "python",
                "ver": "1.0.0",
                "nfs": ("/opt/stability-test-agent/agent/scripts/"
                        "flash_preflight/v1.0.0/flash_preflight.py"),
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "Idempotent host preflight for the flash fleet — Qt "
                    "runtime libs, flashtool exec bit, dialout group, udev "
                    "0666 rule, sudo availability, NFS firmware pointer "
                    "(auto-fix by default)"
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
                "name": "flash_preflight",
                "ver": "1.0.0",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, is_active = false, updated_at = :now "
            "WHERE name = 'flash_preflight' AND version = '1.0.0'"
        ),
        {"now": now},
    )
