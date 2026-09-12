"""seed connect_wifi v1.0.2 — 「已连接」判据假成功修复（#1558）

Revision ID: q3r4s5t6u7v8
Revises: p9q8r7s6t5u4
Create Date: 2026-09-12

Data migration（#1558，审计批次 audit-2026-09-12）：

1. 登记 connect_wifi v1.0.2（自包含、不依赖 scan_script_root 的执行时机）；
2. 停用 v1.0.1。

Behavioral delta vs v1.0.1：

原「已连接」判据是 ``ssid in wifi_status_stdout``（子串匹配）。请求连接
``Test`` 而设备实际连在 ``Test-5G``（同 SSID 的 5G 频段）时返回 True →
直接以 ``skipped=True`` 报成功且**根本不发起连接**，用例以为连上了目标网络；
v1.0.1 新增的「连接后回读复验」用的是同一谓词，同样拦不住。

v1.0.2 改为格式无关的**精确 token 匹配**：按分隔符（空白/引号/逗号/冒号/等号/
括号）切分状态输出后做全等比较，另加带引号全等路径覆盖 SSID 含空格的情形。
不假设 ``cmd -w wifi status`` 的具体标签形态——仓库内只有
``Wifi is enabled`` / ``Wifi is not connected`` 两行有真实设备样本，
连上后的 SSID 行形态没有样本，故不猜格式。失败方向是保守的假阴性
（多连一次并在 10s 窗口内复验），不再有假成功。

参数仍由平台 ResourcePool 注入（``inject_wifi_params`` 只改 action 含
``connect_wifi`` 的步骤），脚本侧 default_params 保持为空。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "q3r4s5t6u7v8"
down_revision = "p9q8r7s6t5u4"
branch_labels = None
depends_on = None

PARAM_SCHEMA = {
    "ssid": {
        "type": "string",
        "required": False,
        "label": "SSID",
        "description": "缺省由平台 ResourcePool 注入（STP_WIFI_SSID）",
    },
    "password": {
        "type": "string",
        "required": False,
        "label": "密码",
        "description": "缺省由平台 ResourcePool 注入（STP_WIFI_PASSWORD）",
    },
    "timeout_seconds": {
        "type": "integer",
        "required": False,
        "label": "连接超时(秒)",
        "description": "connect-network 命令的超时（默认 30）",
    },
}

DEFAULT_PARAMS: dict = {}

VERSIONS = [
    {
        "name": "connect_wifi", "ver": "1.0.2",
        "sha": "ca8fd32931d7cd3896337073442ebee99cf56f92d772ef04a823ca837b626a78",
        "desc": "连接 WiFi — v1.0.1 + 「已连接」判据改精确匹配（子串假成功修复，#1558）",
        "deactivate": ["1.0.1"],
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        row = conn.execute(
            text("SELECT id FROM script WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"]},
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
                    "name": v["name"], "display": v["name"], "cat": "device",
                    "stype": "python", "ver": v["ver"],
                    "nfs": (f"/opt/stability-test-agent/agent/scripts/"
                            f"{v['name']}/v{v['ver']}/{v['name']}.py"),
                    "sha": v["sha"],
                    "pschema": json.dumps(PARAM_SCHEMA, ensure_ascii=False),
                    "dparams": json.dumps(DEFAULT_PARAMS),
                    "desc": v["desc"], "now": now,
                },
            )
        else:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": v["ver"], "now": now},
            )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        conn.execute(
            text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
            {"name": v["name"], "ver": v["ver"], "now": now},
        )
        for old in v["deactivate"]:
            conn.execute(
                text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                {"name": v["name"], "ver": old, "now": now},
            )
