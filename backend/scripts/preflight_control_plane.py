"""Preflight wrapper: verify templates + run Stage A env audit.

This intentionally composes existing tools so operators can run a single
command during deployment. It does not print secret values.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Control-plane preflight (templates + Stage A audit).")
    p.add_argument(
        "--backend",
        default=os.getenv("STP_AUDIT_BACKEND", "http://127.0.0.1:8000"),
        help="Backend base URL for live probe (env: STP_AUDIT_BACKEND).",
    )
    p.add_argument(
        "--env-file",
        default=os.getenv("STP_AUDIT_ENV_FILE", str(Path(__file__).resolve().parents[1] / ".env")),
        help="Env file path for audit (env: STP_AUDIT_ENV_FILE).",
    )
    p.add_argument(
        "--origin",
        default=os.getenv("STP_SMOKE_ORIGIN", "http://localhost:5173"),
        help="Browser Origin for CSRF probe (env: STP_SMOKE_ORIGIN).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]

    env = os.environ.copy()
    env["STP_AUDIT_BACKEND"] = args.backend
    env["STP_AUDIT_ENV_FILE"] = args.env_file
    env["STP_SMOKE_ORIGIN"] = args.origin

    verify = repo_root / "tools" / "verify_control_plane_templates.py"
    audit = repo_root / "backend" / "scripts" / "audit_stage_a_env.py"

    # 1) Static template invariants
    r1 = subprocess.run([sys.executable, str(verify)], cwd=str(repo_root), env=env)
    if r1.returncode != 0:
        return r1.returncode

    # 2) Live env / health probe
    r2 = subprocess.run([sys.executable, str(audit)], cwd=str(repo_root), env=env)
    return r2.returncode


if __name__ == "__main__":
    raise SystemExit(main())

