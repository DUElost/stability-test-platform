"""seed flash_firmware v1.3.12 + oobe_skip v1.1.1 — 刷机时序加固（#1591）

Revision ID: x3y2z1a0b9c8
Revises: cc33dd44ee55
Create Date: 2026-09-12

Data migration (刷机流程时序失败率修复):

1. Ensure flash_firmware v1.3.12 exists, deactivate v1.3.11.
2. Ensure oobe_skip v1.1.1 exists, deactivate v1.1.0.

Behavioral delta（2026-09-11/12 31 台刷机实证）:
- flash_firmware v1.3.12：
  · fingerprint 前 _wait_model_ready（boot_completed + ro.product.model
    轮询，默认 90s）——刷机后 adb 已连但系统未起时不再直接判失败
  · 失败后 _attempt_device_recovery（adb reboot）——避免停在 BROM/USB 死态
- oobe_skip v1.1.1：验证失败重试（verify_retries 默认 2，重设 flags +
  等待 + 重验证）——刷机后 settings 未即时落盘场景收敛
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "x3y2z1a0b9c8"
down_revision = "cc33dd44ee55"
branch_labels = None
depends_on = None

VERSIONS = [
    {"name": "flash_firmware", "ver": "1.3.12", "sha": "f31451339f65d1b2b61b904bc5d6aed518961085f10dd5fb04a1681646ad4cfc",
     "desc": "刷机 — v1.3.11 + fingerprint 就绪等待 + 失败后设备态恢复（#1591）",
     "deactivate": ["1.3.11"]},
    {"name": "oobe_skip", "ver": "1.1.1", "sha": "945bfd32d2b61b5af1d00377a3ab0095a69997384bca073192da6998cf3e4325",
     "desc": "OOBE 跳过 — v1.1.0 + 验证失败重试（#1591）",
     "deactivate": ["1.1.0"]},
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
                {"name": v["name"], "display": v["name"], "cat": "device",
                  "stype": "python", "ver": v["ver"],
                  "nfs": (f"/opt/stability-test-agent/agent/scripts/"
                          f"{v['name']}/v{v['ver']}/{v['name']}.py"),
                  "sha": v["sha"], "pschema": json.dumps({}), "dparams": json.dumps({}),
                  "desc": v["desc"], "now": now},
            )
        else:
            conn.execute(text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                         {"name": v["name"], "ver": v["ver"], "now": now})
        for old in v["deactivate"]:
            conn.execute(text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
                         {"name": v["name"], "ver": old, "now": now})


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)
    for v in VERSIONS:
        conn.execute(text("UPDATE script SET is_active = false, updated_at = :now WHERE name = :name AND version = :ver"),
                     {"name": v["name"], "ver": v["ver"], "now": now})
        for old in v["deactivate"]:
            conn.execute(text("UPDATE script SET is_active = true, updated_at = :now WHERE name = :name AND version = :ver"),
                         {"name": v["name"], "ver": old, "now": now})
