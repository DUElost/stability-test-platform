"""PlanRun 52: 确认 Jira 提单目录并触发 extract（长超时）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

BASE = "http://172.21.10.25:8000"
ORIGIN = os.getenv("STP_SMOKE_ORIGIN", "http://localhost:5173")
RUN_ID = 52
NFS = Path(r"Y:\sonic_tinno")


def main() -> None:
    password = os.getenv("STP_ADMIN_PASSWORD")
    if not password:
        die("set STP_ADMIN_PASSWORD")

    h = {"Origin": ORIGIN, "Referer": f"{ORIGIN}/"}
    with httpx.Client(base_url=BASE, timeout=600.0) as c:
        r = c.post("/api/v1/auth/login", data={"username": "admin", "password": password}, headers=h)
        if r.status_code != 200:
            die(f"login {r.status_code}")

        print("=== dedup/status before ===")
        for a in _artifacts(c, RUN_ID):
            print(f"  {a['artifact_type']}: {a['storage_uri']}")

        print("\n=== re-merge ===")
        r = c.post(f"/api/v1/plan-runs/{RUN_ID}/dedup/merge", headers=h)
        print(f"merge {r.status_code} {r.text[:300]}")

        print("\n=== dedup/status after merge ===")
        arts = _artifacts(c, RUN_ID)
        for a in arts:
            print(f"  {a['artifact_type']}: {a['storage_uri']}")

        print("\n=== extract (timeout 600s) ===")
        r = c.post(f"/api/v1/plan-runs/{RUN_ID}/dedup/extract", headers=h)
        print(f"extract {r.status_code} {r.text[:500]}")

    jira = NFS / "jira" / str(RUN_ID)
    devices = NFS / "devices" / str(RUN_ID)
    if jira.is_dir():
        dirs = sum(1 for p in jira.iterdir() if p.is_dir())
        files = sum(1 for p in jira.rglob("*") if p.is_file())
        xls = list(jira.glob("*.xls"))
        print(f"\n=== jira/{RUN_ID} ===")
        print(f"  dirs={dirs} files={files} xls={[x.name for x in xls]}")
    if devices.is_dir():
        dev_dirs = sum(1 for p in devices.iterdir() if p.is_dir())
        print(f"  devices/{RUN_ID} source dirs={dev_dirs}")

    # 补拷 Run52 dedup org（Jira upload_list 主输入）
    dedup_org = NFS / "dedup" / str(RUN_ID)
    if dedup_org.is_dir() and jira.is_dir():
        for src in dedup_org.glob("*dedup_org*.xls"):
            dest = jira / src.name
            if not dest.exists():
                import shutil
                shutil.copy2(src, dest)
                print(f"  copied dedup xls -> {dest.name}")


def _artifacts(c: httpx.Client, run_id: int) -> list:
    r = c.get(f"/api/v1/plan-runs/{run_id}/dedup/status")
    body = r.json().get("data", r.json())
    return body.get("artifacts") or []


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
