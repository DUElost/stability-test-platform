#!/usr/bin/env python3
"""Post-release deploy readiness checks (read-only).

Usage (from repo root)::

    ./venv/bin/python tools/dev/check-deploy-readiness.py
    ./venv/bin/python tools/dev/check-deploy-readiness.py --expect-revision k8l9m0n1o2p3

Resolves ``DATABASE_URL`` from ambient env first, then repo-root ``.env.backend``.
All DB access is SELECT-only. Exit 0 when checks pass; 1 when actionable issues found.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 删除兼容回落后必须存在的新键（#518：无前缀键回落删除后，生产只配旧键会
# resolve_scan_tool() 返回 None → merge 静默跳过，报表缺失却照报 SUCCESS）。
# 凡「删除兼容回落」的 PR 必须**同 PR**在此追加新键名；此处是阻塞项，缺失即部署失败。
_REQUIRED_ENV_KEYS: tuple[str, ...] = (
    "STP_BACKEND_DEDUP_SCAN_PYTHON",
    "STP_BACKEND_DEDUP_SCAN_SCRIPT",
)

_UNBOUND_MTBF_SQL = """
SELECT DISTINCT p.id, p.name
FROM plan p
JOIN plan_step ps ON ps.plan_id = p.id
WHERE ps.script_name LIKE 'mtbf\\_%'
  AND ps.enabled IS NOT FALSE
  AND p.suite_id IS NULL
ORDER BY p.id
"""


def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        env = _load_env_file(_REPO_ROOT / ".env.backend")
        url = (env.get("DATABASE_URL") or "").strip()
        if not url:
            env = _load_env_file(_REPO_ROOT / ".env")
            url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        raise SystemExit("DATABASE_URL not found in ambient env / .env.backend / .env")
    # psycopg.connect() needs plain postgresql:// (not +asyncpg / +psycopg / +psycopg2).
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", url, count=1)


def _load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE env file (handles ``export `` prefix and quotes)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-revision",
        default=None,
        help="Optional alembic revision to assert (e.g. k8l9m0n1o2p3)",
    )
    args = parser.parse_args()

    issues: list[str] = []
    url = _database_url()

    # 阻塞项：删除兼容回落后必须存在的键（#518 教训——只配旧键 → merge 静默跳过）。
    # 生效源 = ambient env 或仓库根 .env.backend（生产唯一 env 源）。
    env_file = _load_env_file(_REPO_ROOT / ".env.backend")
    missing = [k for k in _REQUIRED_ENV_KEYS if k not in os.environ and k not in env_file]
    if missing:
        issues.append(
            "required env 键缺失（兼容回落已删，缺失即静默跳过 merge）："
            + ", ".join(missing)
        )
    else:
        print("[env] required keys present: " + ", ".join(_REQUIRED_ENV_KEYS))

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
            row = cur.fetchone()
            revision = row[0] if row else None
            print(f"[alembic] version_num={revision!r}")
            if args.expect_revision and revision != args.expect_revision:
                issues.append(
                    f"alembic_version {revision!r} != expected {args.expect_revision!r}"
                )

            cur.execute(_UNBOUND_MTBF_SQL)
            unbound = cur.fetchall()
            if unbound:
                issues.append(
                    f"{len(unbound)} mtbf plan(s) missing suite_id (SUITE_BINDING_REQUIRED at dispatch)"
                )
                for plan_id, name in unbound:
                    print(f"  [mtbf-unbound] plan_id={plan_id} name={name!r}")
            else:
                print("[mtbf] all enabled mtbf_* plans have suite_id bound")

    print("\n[reminders] manual post-deploy (not auto-verified):")
    print("  - restart stability-backend after alembic upgrade head")
    print("  - POST /api/v1/scripts/scan to register new script versions")
    print("  - restart all stability-test-agent hosts (#514 fail-fast / claim cap / step-trace drain)")
    print("  - verify Agent HOST_ID matches hosts.id after k8l9 migration")
    print("  - optional: grep fleet .env for stale STP_MTBF_EXPECTED_TESTPOINT_COUNT")

    if issues:
        print("\n[FAIL]", file=sys.stderr)
        for item in issues:
            print(f"  - {item}", file=sys.stderr)
        raise SystemExit(1)

    print("\n[OK] automated checks passed")


if __name__ == "__main__":
    main()
