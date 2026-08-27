"""seed flash_preflight v1.0.1 params and deactivate v1.0.0

Revision ID: h2i3j4k5l6m7
Revises: g1h2i3j4k5l6
Create Date: 2026-08-27

Data migration (Debian 13 t64 改名兼容):

1. Ensure flash_preflight v1.0.1 exists in the script table with populated
   param_schema and default_params (g1h2i3j4k5l6 precedent).
2. Deactivate v1.0.0 — same-day bugfix whose failure mode is benign
   (false-negative apt verdict; rerunning the new version suffices).

Behavioral delta vs v1.0.0: dpkg probe now falls back to the t64 variant
(libglib2.0-0 -> libglib2.0-0t64) when the literal name reports the
transitional-package status. v1.0.0's first production run (.66, PlanRun
#234) reported "qt-libs: apt install failed rc=0" because apt installs
the alias with rc=0 while the literal-name recheck never matches.

Params identical to v1.0.0. nfs_path stays absolute per platform
convention.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "h2i3j4k5l6m7"
down_revision = "g1h2i3j4k5l6"
branch_labels = None
depends_on = None

PARAM_SCHEMA = {
    "fix": {
        "type": "boolean",
        "required": False,
        "label": "自动修复",
        "description": "true=检查→缺则自动修复（默认）；false=只检不修",
        "default": True,
    },
    "skip_apt": {
        "type": "boolean",
        "required": False,
        "label": "跳过包类检查",
        "description": "无外网/apt 镜像的 host 置 true；默认 false",
        "default": False,
    },
    "dialout_user": {
        "type": "string",
        "required": False,
        "label": "dialout 目标用户",
        "description": "缺省取运行本脚本的当前用户",
        "default": "android",
    },
}

DEFAULT_PARAMS = {
    "fix": True,
    "skip_apt": False,
    "dialout_user": "android",
}

# sha256 of flash_preflight.py v1.0.1
_CONTENT_SHA256 = "46d6d96b208cd935a15ed58e2bcbe174b0458d28b7be2f54160c2b3bd9342f08"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "flash_preflight", "ver": "1.0.1"},
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
                "ver": "1.0.1",
                "nfs": ("/opt/stability-test-agent/agent/scripts/"
                        "flash_preflight/v1.0.1/flash_preflight.py"),
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "Idempotent host preflight for the flash fleet — v1.0.0 + "
                    "Debian 13 t64 dpkg probe fallback (libglib2.0-0t64)"
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
                "ver": "1.0.1",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'flash_preflight' AND version = '1.0.0'"
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
            "WHERE name = 'flash_preflight' AND version = '1.0.1'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'flash_preflight' AND version = '1.0.0'"
        ),
        {"now": now},
    )
