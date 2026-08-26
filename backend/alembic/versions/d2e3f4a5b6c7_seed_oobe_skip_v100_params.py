"""seed oobe_skip v1.0.0 params

Revision ID: d2e3f4a5b6c7
Revises: c9d0e1f2a3b4
Create Date: 2026-08-26

Data migration (OOBE 跳过平台化 · B 方案):

1. Ensure oobe_skip v1.0.0 exists in the script table with populated
   param_schema and default_params — self-contained and deployment-order
   independent w.r.t. scan_script_root (a7b8c9d0e1f2 precedent).

Script semantics: post-flash companion step for flash_firmware — skips the
OOBE first page on exactly ONE device (every adb command is scoped with
`-s STP_DEVICE_SERIAL`), because a phone parked on OOBE powers itself off
after prolonged screen-on idle (field finding 2026-08-26, see
docs/notes/feature/2026-08-25-mtk-flash-fleet-automation.md §6).

default_params carries all three knobs: none of them has an env escape
hatch today, and their desired values are fixed platform-wide (unlike the
flash_firmware fleet keys, there is no per-host reason to vary them).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "d2e3f4a5b6c7"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

# ── oobe_skip v1.0.0 metadata ───────────────────────────────────────────────

PARAM_SCHEMA = {
    "wait_for_device_seconds": {
        "type": "integer",
        "required": False,
        "label": "等设备回网(秒)",
        "description": (
            "刷完重启后等目标设备回到 adb 的上限；0 = 不等待。默认 120"
        ),
        "default": 120,
        "minimum": 0,
    },
    "locales": {
        "type": "string",
        "required": False,
        "label": "系统语言",
        "description": "写入 system_locales 的值；默认 en-US",
        "default": "en-US",
    },
    "verify_setup_complete": {
        "type": "boolean",
        "required": False,
        "label": "回读核验",
        "description": (
            "写完后回读 user_setup_complete/device_provisioned，"
            "非 1 判失败；默认 true"
        ),
        "default": True,
    },
}

DEFAULT_PARAMS = {
    "wait_for_device_seconds": 120,
    "locales": "en-US",
    "verify_setup_complete": True,
}

# sha256 of oobe_skip.py v1.0.0
_CONTENT_SHA256 = "f00d59f090b38a117c7a5c0c0340e787ea457c7dff4e5c4f689cda0149ddc1c0"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "oobe_skip", "ver": "1.0.0"},
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
                "name": "oobe_skip",
                "display": "oobe_skip",
                "cat": "device",
                "stype": "python",
                "ver": "1.0.0",
                "nfs": "oobe_skip/v1.0.0/oobe_skip.py",
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "Skip Android OOBE on one serial-scoped device after "
                    "flash_firmware (settings flags + HOME intent; never "
                    "broadcasts to other adb devices)"
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
                "name": "oobe_skip",
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
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'oobe_skip' AND version = '1.0.0'"
        ),
        {"now": now},
    )
