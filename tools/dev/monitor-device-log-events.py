#!/usr/bin/env python3
"""ADR-0028 acceptance monitor — read-only snapshot of device_log_event.

Usage (from repo root)::

    ./venv/bin/python tools/dev/monitor-device-log-events.py
    ./venv/bin/python tools/dev/monitor-device-log-events.py --host-id 172-21-8-143
    ./venv/bin/python tools/dev/monitor-device-log-events.py --plan-run-id 199 --limit 10

Resolves ``DATABASE_URL`` from ambient env first, then repo-root ``.env.backend``
(see AGENTS.md §Production access). Read-only SELECT only.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg


def _database_url() -> str:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url:
        for name in (".env.backend", ".env"):
            path = Path(__file__).resolve().parents[2] / name
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DATABASE_URL="):
                    url = line.split("=", 1)[1].strip().strip('"')
                    break
            if url:
                break
    if not url:
        raise SystemExit("DATABASE_URL not found in ambient env / .env.backend / .env")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _filters(
    *,
    host_id: str | None,
    plan_run_id: int | None,
    alias: str = "",
) -> tuple[str, list[object]]:
    prefix = f"{alias}." if alias else ""
    clauses = ["TRUE"]
    params: list[object] = []
    if host_id:
        clauses.append(f"{prefix}host_id = %s")
        params.append(host_id)
    if plan_run_id is not None:
        clauses.append(f"{prefix}plan_run_id = %s")
        params.append(plan_run_id)
    return " AND ".join(clauses), params


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-id", help="filter by host_id")
    parser.add_argument("--plan-run-id", type=int, help="filter by plan_run_id")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    where, params = _filters(host_id=args.host_id, plan_run_id=args.plan_run_id)
    where_d, params_d = _filters(
        host_id=args.host_id, plan_run_id=args.plan_run_id, alias="d",
    )
    url = _database_url()
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT state, COUNT(*)::bigint
                FROM device_log_event
                WHERE {where}
                GROUP BY state
                ORDER BY state
                """,
                params,
            )
            print("=== state counts ===")
            rows = cur.fetchall()
            if not rows:
                print("(no rows)")
            for state, count in rows:
                print(f"  {state}: {count}")

            cur.execute(
                f"""
                SELECT event_type, COUNT(*)::bigint
                FROM device_log_event
                WHERE {where}
                GROUP BY event_type
                ORDER BY COUNT(*) DESC, event_type
                """,
                params,
            )
            print("\n=== event_type counts ===")
            for event_type, count in cur.fetchall():
                print(f"  {event_type}: {count}")

            cur.execute(
                f"""
                SELECT
                  COUNT(*)::bigint,
                  COUNT(*) FILTER (WHERE signal_seq_no IS NOT NULL)::bigint
                FROM device_log_event
                WHERE {where}
                """,
                params,
            )
            dle, with_seq = cur.fetchone()
            cur.execute(
                f"""
                SELECT COUNT(*)::bigint
                FROM job_log_signal s
                JOIN device_log_event d ON d.id = s.device_log_event_id
                WHERE {where_d}
                """,
                params_d,
            )
            linked = cur.fetchone()[0]
            print("\n=== signal link ===")
            print(f"  dle={dle} with_signal_seq_no={with_seq} job_log_signal.linked={linked}")

            cur.execute(
                f"""
                SELECT id, host_id, serial, state, event_type,
                       detected_at, local_path, remote_path, plan_run_id,
                       signal_seq_no, updated_at
                FROM device_log_event
                WHERE {where}
                ORDER BY detected_at DESC
                LIMIT %s
                """,
                [*params, args.limit],
            )
            print(f"\n=== latest {args.limit} events ===")
            for row in cur.fetchall():
                print("---")
                print(
                    f"id={row[0]} host={row[1]} serial={row[2]} "
                    f"state={row[3]} type={row[4]} seq={row[9]}"
                )
                print(
                    f"detected_at={row[5]} plan_run_id={row[8]} updated_at={row[10]}"
                )
                print(f"local={row[6]}")
                print(f"remote={row[7]}")


if __name__ == "__main__":
    main()
