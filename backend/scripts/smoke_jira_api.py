"""Smoke test POST /api/v1/jira/runs after backend STP_JIRA_* config."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = os.getenv("STP_VERIFY_BACKEND", "http://172.21.10.25:8000")
ORIGIN = os.getenv("STP_SMOKE_ORIGIN", "http://localhost:5173")
SAMPLE_XLS = Path(
    r"Y:\sonic_tinno\jira\52"
    r"\auto-fdaf1d55e319_Result_None_None_MonkeyAEE_SH_20260627_052222"
    r"_org_dedup_org_20260627_052224.xls"
)
UPLOAD_TEMPLATE = Path(
    r"F:\automation-toolkit\python-tools\stability_Jira-Automation\_verify_run52"
    r"\JIRA_Upload_List_transsion_verify_20260627_175023.xlsx"
)


def poll_run(c: httpx.Client, run_id: str, *, timeout_sec: int = 90) -> dict:
    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        st = c.get(f"/api/v1/jira/runs/{run_id}").json()["data"]
        status = st.get("status")
        if status != last:
            print(f"  [{run_id}] status={status} exit={st.get('exit_code')}")
            last = status
        if status in ("SUCCESS", "FAILED", "CANCELED"):
            return st
        time.sleep(2)
    raise TimeoutError(f"run {run_id} still {last}")


def tail_log(c: httpx.Client, run_id: str, n: int = 6) -> None:
    log = c.get(f"/api/v1/jira/runs/{run_id}/log?from_seq=0").json()["data"]
    for line in (log.get("lines") or [])[-n:]:
        print(f"    {line[:200]}")


def main() -> None:
    password = os.getenv("STP_ADMIN_PASSWORD")
    if not password:
        die("STP_ADMIN_PASSWORD required")

    h = {"Origin": ORIGIN, "Referer": f"{ORIGIN}/"}
    with httpx.Client(base_url=BASE, timeout=120.0) as c:
        r = c.post("/api/v1/auth/login", data={"username": "admin", "password": password}, headers=h)
        print("login", r.status_code)
        if r.status_code != 200:
            die(f"login failed: {r.text[:200]}")

        if not SAMPLE_XLS.is_file():
            die(f"sample xls missing: {SAMPLE_XLS}")

        print("\n=== transsion upload_list (1-row dedup xls) ===")
        with SAMPLE_XLS.open("rb") as f:
            r = c.post(
                "/api/v1/jira/runs",
                data={"vendor": "transsion", "stage": "upload_list", "dry_run": "true"},
                files={"file": ("one_row_sample.xls", f.read(), "application/vnd.ms-excel")},
                headers=h,
            )
        print("start", r.status_code, r.text[:300])
        if r.status_code != 200:
            die("upload_list failed")
        run1 = r.json()["data"]["console_run_id"]
        st1 = poll_run(c, run1)
        tail_log(c, run1)
        if st1.get("status") != "SUCCESS" or st1.get("exit_code") not in (0, None):
            die(f"upload_list run failed: {st1}")

        if not UPLOAD_TEMPLATE.is_file():
            die(f"upload template missing: {UPLOAD_TEMPLATE}")

        print("\n=== transsion create dry-run (1-row upload template) ===")
        with UPLOAD_TEMPLATE.open("rb") as f:
            r = c.post(
                "/api/v1/jira/runs",
                data={"vendor": "transsion", "stage": "create", "dry_run": "true"},
                files={"file": ("JIRA_Upload_List.xlsx", f.read(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                headers=h,
            )
        print("start", r.status_code, r.text[:300])
        if r.status_code != 200:
            die("create dry-run start failed")
        run2 = r.json()["data"]["console_run_id"]
        st2 = poll_run(c, run2, timeout_sec=120)
        tail_log(c, run2)
        if st2.get("status") != "SUCCESS" or st2.get("exit_code") not in (0, None):
            die(f"create dry-run failed: {st2}")

    print("\n>>> API Jira smoke PASS (upload_list + create dry-run, transsion, 1 row each) <<<")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
