"""seed oobe_skip v1.1.0 params and deactivate v1.0.0

Revision ID: d3e4f5a6b7c8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-26

Data migration (OOBE 跳过真机配方修正):

1. Ensure oobe_skip v1.1.0 exists in the script table with populated
   param_schema and default_params (d2e3f4a5b6c7 precedent).
2. Deactivate v1.0.0 (superseded same-day after field falsification).

Behavioral delta vs v1.0.0 (real-device evidence 2026-08-26 on .66):
- readiness now additionally requires sys.boot_completed=1 — get-state
  alone passes during the DA-hybrid boot phase while SetupWizard is still
  initializing, so early commands got overridden when SUW woke up
  ("success but still on OOBE" root cause);
- SetupWizard is force-stopped explicitly (flags alone never dismiss a
  foreground SUW), then wake(224)/unlock(82)/HOME(3) key sequence;
- ui_focus diagnostic captured from dumpsys window (non-authoritative);
- new param setupwizard_package (default com.google.android.setupwizard).

All knobs stay in default_params: fixed desired values, no env escapes.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "d3e4f5a6b7c8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None

# ── oobe_skip v1.1.0 metadata ───────────────────────────────────────────────

PARAM_SCHEMA = {
    "wait_for_device_seconds": {
        "type": "integer",
        "required": False,
        "label": "就绪等待(秒)",
        "description": (
            "刷完重启后等设备 adb 可见且 boot_completed=1 的总预算；"
            "0 = 不等待。默认 120"
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
    "setupwizard_package": {
        "type": "string",
        "required": False,
        "label": "SetupWizard 包名",
        "description": (
            "清场时 force-stop 的开机向导包名；"
            "默认 com.google.android.setupwizard"
        ),
        "default": "com.google.android.setupwizard",
    },
}

DEFAULT_PARAMS = {
    "wait_for_device_seconds": 120,
    "locales": "en-US",
    "verify_setup_complete": True,
    "setupwizard_package": "com.google.android.setupwizard",
}

# sha256 of oobe_skip.py v1.1.0
_CONTENT_SHA256 = "d2ccde9bd0a968d0b0a77491f4c59e7e7b50cf745b108b42658348aba101d6f3"


def upgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    row = conn.execute(
        text("SELECT id FROM script WHERE name = :name AND version = :ver"),
        {"name": "oobe_skip", "ver": "1.1.0"},
    ).fetchone()

    if row is None:
        prior = conn.execute(
            text(
                "SELECT nfs_path FROM script "
                "WHERE name = 'oobe_skip' AND version = '1.0.0'"
            ),
        ).fetchone()
        nfs_path = (
            prior.nfs_path.replace("/v1.0.0/", "/v1.1.0/") if prior
            else "oobe_skip/v1.1.0/oobe_skip.py"
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
                "name": "oobe_skip",
                "display": "oobe_skip",
                "cat": "device",
                "stype": "python",
                "ver": "1.1.0",
                "nfs": nfs_path,
                "sha": _CONTENT_SHA256,
                "pschema": json.dumps(PARAM_SCHEMA),
                "dparams": json.dumps(DEFAULT_PARAMS),
                "desc": (
                    "Skip Android OOBE on one serial-scoped device — "
                    "v1.0.0 + boot_completed gate, SUW force-stop, focus "
                    "diagnostic (field-verified recipe)"
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
                "ver": "1.1.0",
                "dparams": json.dumps(DEFAULT_PARAMS),
                "pschema": json.dumps(PARAM_SCHEMA),
                "now": now,
            },
        )

    # ── Deactivate superseded version (no rollback retention needed for a ──
    # ── same-day fix whose failure mode is benign: rerun the new version) ──
    conn.execute(
        text(
            "UPDATE script SET is_active = false, updated_at = :now "
            "WHERE name = 'oobe_skip' AND version = '1.0.0'"
        ),
        {"now": now},
    )


def downgrade() -> None:
    conn = op.get_bind()
    now = datetime.now(timezone.utc)

    conn.execute(
        text(
            "UPDATE script SET default_params = '{}'::jsonb, "
            " param_schema = '{}'::jsonb, updated_at = :now "
            "WHERE name = 'oobe_skip' AND version = '1.1.0'"
        ),
        {"now": now},
    )
    conn.execute(
        text(
            "UPDATE script SET is_active = true, updated_at = :now "
            "WHERE name = 'oobe_skip' AND version = '1.0.0'"
        ),
        {"now": now},
    )
