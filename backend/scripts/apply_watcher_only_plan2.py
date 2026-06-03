"""One-off: Plan #2 watcher-only (remove scan_aee / export_mobilelogs) + optional abort + trigger.

DEV: reads repo .env STP_ADMIN_PASSWORD, cookie login to local backend.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from backend.scripts.seed_and_smoke import load_repo_dotenv, build_csrf_headers, default_smoke_origin, _unwrap

PLAN_ID = 2
DEVICE_IDS = [62]
ABORT_RUN_IDS = [6]

WATCHER_ONLY_STEPS = [
    {"step_key": "check_device", "script_name": "check_device", "script_version": "1.0.0",
     "stage": "init", "sort_order": 0, "timeout_seconds": 30, "retry": 1, "enabled": True},
    {"step_key": "ensure_root", "script_name": "ensure_root", "script_version": "1.0.0",
     "stage": "init", "sort_order": 1, "timeout_seconds": 60, "retry": 1, "enabled": True},
    {"step_key": "monkey_setup", "script_name": "monkey_setup", "script_version": "1.0.0",
     "stage": "init", "sort_order": 2, "timeout_seconds": 600, "retry": 1, "enabled": True},
    {"step_key": "monkey_resource_push", "script_name": "monkey_resource_push", "script_version": "1.0.0",
     "stage": "init", "sort_order": 3, "timeout_seconds": 600, "retry": 1, "enabled": True},
    {"step_key": "monkey_launch", "script_name": "monkey_launch", "script_version": "5.0.0",
     "stage": "init", "sort_order": 4, "timeout_seconds": 1200, "retry": 0, "enabled": True},
    {"step_key": "monkey_check", "script_name": "monkey_check", "script_version": "2.0.2",
     "stage": "patrol", "sort_order": 0, "timeout_seconds": 60, "retry": 0, "enabled": True},
    {"step_key": "monkey_teardown", "script_name": "monkey_teardown", "script_version": "1.0.0",
     "stage": "teardown", "sort_order": 0, "timeout_seconds": 600, "retry": 0, "enabled": True},
]


def main() -> int:
    load_repo_dotenv(_REPO)
    base = os.getenv("STP_SMOKE_BACKEND", "http://127.0.0.1:8000").rstrip("/")
    origin = default_smoke_origin()
    password = os.getenv("STP_ADMIN_PASSWORD")
    username = os.getenv("STP_ADMIN_USER", "admin")
    if not password:
        print("STP_ADMIN_PASSWORD missing in .env", file=sys.stderr)
        return 1

    headers = build_csrf_headers(origin)
    with httpx.Client(base_url=base, timeout=60.0, headers=headers) as client:
        r = client.post(
            "/api/v1/auth/login",
            data={"username": username, "password": password},
        )
        if r.status_code != 200:
            print(f"login failed {r.status_code}: {r.text}", file=sys.stderr)
            return 1
        print("login ok")

        g = client.get(f"/api/v1/plans/{PLAN_ID}")
        if g.status_code != 200:
            print(f"get plan failed: {g.text}", file=sys.stderr)
            return 1
        cur = _unwrap(g.json())
        patrol_before = [s["step_key"] for s in cur.get("steps", []) if s.get("stage") == "patrol"]
        print(f"plan {PLAN_ID} patrol before: {patrol_before}")

        payload = {
            "description": "AEE via Watcher/reconciler only (no scan_aee patrol)",
            "patrol_interval_seconds": cur.get("patrol_interval_seconds") or 300,
            "timeout_seconds": cur.get("timeout_seconds") or 604800,
            "failure_threshold": cur.get("failure_threshold", 0.05),
            "watcher_policy": {"enabled": True},
            "steps": WATCHER_ONLY_STEPS,
        }
        u = client.put(f"/api/v1/plans/{PLAN_ID}", json=payload)
        if u.status_code != 200:
            print(f"put plan failed {u.status_code}: {u.text}", file=sys.stderr)
            return 1
        after = _unwrap(u.json())
        patrol_after = [s["step_key"] for s in after.get("steps", []) if s.get("stage") == "patrol"]
        print(f"plan {PLAN_ID} patrol after: {patrol_after}")

        pv = client.post(f"/api/v1/plans/{PLAN_ID}/run/preview", json={"device_ids": DEVICE_IDS})
        if pv.status_code == 200:
            life = _unwrap(pv.json()).get("lifecycle", {})
            psteps = (life.get("patrol") or {}).get("steps") or []
            print(f"preview patrol steps: {[s.get('step_id') for s in psteps]}")

        for run_id in ABORT_RUN_IDS:
            s = client.get(f"/api/v1/plan-runs/{run_id}")
            if s.status_code != 200:
                print(f"plan_run {run_id}: get {s.status_code}")
                continue
            st = _unwrap(s.json()).get("status")
            print(f"plan_run {run_id} status={st}")
            if st == "RUNNING":
                a = client.post(
                    f"/api/v1/plan-runs/{run_id}/abort",
                    json={"reason": "switch to watcher-only plan"},
                )
                print(f"abort {run_id}: {a.status_code} {_unwrap(a.json()) if a.status_code == 200 else a.text}")

        tr = client.post(f"/api/v1/plans/{PLAN_ID}/run", json={"device_ids": DEVICE_IDS})
        if tr.status_code not in (200, 201):
            print(f"trigger failed {tr.status_code}: {tr.text}", file=sys.stderr)
            return 1
        run = _unwrap(tr.json())
        print(f"triggered plan_run_id={run.get('id')} status={run.get('status')}")
        print(json.dumps({"plan_id": PLAN_ID, "plan_run_id": run.get("id"), "device_ids": DEVICE_IDS}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
