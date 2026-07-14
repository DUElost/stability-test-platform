"""Batch hot-update all ONLINE hosts with no active jobs.

Usage:
    # Via API (needs STP_ADMIN_PASSWORD, subject to login rate limit):
    STP_ADMIN_PASSWORD=... STP_SMOKE_ORIGIN=http://127.0.0.1 \\
      PYTHONPATH=. python backend/scripts/batch_hot_update.py

    # Direct SSH (no API auth; uses DB + ansible inventory):
    PYTHONPATH=. python backend/scripts/batch_hot_update.py --direct

    PYTHONPATH=. python backend/scripts/batch_hot_update.py --direct --include-active --abort-running-jobs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

from backend.scripts.seed_and_smoke import (
    APIClient,
    _unwrap,
    default_smoke_origin,
    load_repo_dotenv,
)
from backend.services.host_updater import get_agent_code_version


def _list_hosts(client: APIClient) -> list[dict]:
    hosts: list[dict] = []
    skip = 0
    while True:
        resp = client.get("/api/v1/hosts", params={"skip": skip, "limit": 100})
        if resp.status_code != 200:
            raise RuntimeError(f"list hosts failed: {resp.status_code} {resp.text[:300]}")
        body = _unwrap(resp.json())
        if isinstance(body, dict) and "items" in body:
            batch = body["items"]
        elif isinstance(body, list):
            batch = body
        else:
            raise RuntimeError(f"unexpected hosts response: {body!r}")
        if not batch:
            break
        hosts.extend(batch)
        if len(batch) < 100:
            break
        skip += 100
    return hosts


def _hot_update_direct(
    *,
    include_active: bool,
    abort_running_jobs: bool,
) -> int:
    from backend.core.database import SessionLocal
    from backend.core.ssh_security import SshSecurityConfigError, resolve_host_ssh_credentials
    from backend.models.enums import JobStatus
    from backend.models.host import Host
    from backend.models.job import JobInstance
    from backend.services.host_updater import (
        _resolve_ssh_creds,
        execute_hot_update,
        get_agent_code_version,
    )
    from backend.services.agent_version_info import record_agent_code_deployed

    active_statuses = {
        JobStatus.PENDING.value,
        JobStatus.RUNNING.value,
        JobStatus.UNKNOWN.value,
    }
    expected = get_agent_code_version()
    print(f"control_plane_code_version={expected!r}")

    results: list[dict] = []
    with SessionLocal() as db:
        hosts = (
            db.query(Host)
            .filter(Host.status == "ONLINE")
            .order_by(Host.hostname)
            .all()
        )
        print(f"online_hosts={len(hosts)}")
        for host in hosts:
            active = (
                db.query(JobInstance)
                .filter(
                    JobInstance.host_id == host.id,
                    JobInstance.status.in_(active_statuses),
                )
                .count()
            )
            row: dict = {
                "host_id": host.id,
                "hostname": host.hostname,
                "ip": host.ip,
                "active_jobs": active,
            }
            if active and not include_active:
                row["skipped"] = "active_jobs"
                print(f"  SKIP {host.hostname} active_jobs={active}")
                results.append(row)
                continue
            if active and abort_running_jobs:
                print(
                    f"  WARN {host.hostname} has active_jobs={active} "
                    "(--abort-running-jobs not implemented in --direct mode; skip)"
                )
                row["skipped"] = "active_jobs_abort_not_supported_in_direct"
                results.append(row)
                continue
            try:
                creds, _ = resolve_host_ssh_credentials(
                    host, inventory_lookup=_resolve_ssh_creds,
                )
            except SshSecurityConfigError as exc:
                row["ok"] = False
                row["error"] = str(exc)
                print(f"  FAIL {host.hostname} ssh_config: {exc}")
                results.append(row)
                continue
            if not creds.password and not creds.key_path:
                row["ok"] = False
                row["error"] = "no_ssh_credentials"
                print(f"  FAIL {host.hostname} no ssh creds")
                results.append(row)
                continue

            print(f"\n=== hot-update {host.hostname} ({host.ip}) ===")
            result = execute_hot_update(
                host_ip=host.ip or "",
                ssh_port=host.ssh_port or 22,
                ssh_user=creds.user,
                ssh_password=creds.password,
                ssh_key_path=creds.key_path,
                known_hosts_path=creds.known_hosts_path,
                code_version=expected,
            )
            row.update(result)
            if result.get("ok"):
                record_agent_code_deployed(host, expected)
                db.commit()
            print(
                f"  {'OK' if result.get('ok') else 'FAIL'} "
                f"deps_refreshed={result.get('deps_refreshed')} "
                f"msg={(result.get('message') or '')[:120]}"
            )
            results.append(row)

    ok = sum(1 for r in results if r.get("ok") is True)
    fail = sum(1 for r in results if r.get("ok") is False)
    skipped = sum(1 for r in results if r.get("skipped"))
    print(f"\nSUMMARY ok={ok} fail={fail} skipped={skipped}")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if fail == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch hot-update ONLINE hosts")
    parser.add_argument(
        "--direct",
        action="store_true",
        help="bypass HTTP API; SSH hot-update via DB host list (no login rate limit)",
    )
    parser.add_argument(
        "--include-active",
        action="store_true",
        help="also hot-update hosts that currently have active jobs",
    )
    parser.add_argument(
        "--abort-running-jobs",
        action="store_true",
        help="pass abort_running_jobs=true (only with --include-active)",
    )
    parser.add_argument(
        "--retry-abort-pending",
        action="store_true",
        default=True,
        help="retry HOST_ABORT_PENDING hosts once after grace (default: on)",
    )
    args = parser.parse_args()

    if args.direct:
        return _hot_update_direct(
            include_active=args.include_active,
            abort_running_jobs=args.abort_running_jobs,
        )

    load_repo_dotenv()
    password = os.getenv("STP_ADMIN_PASSWORD")
    if not password:
        print("STP_ADMIN_PASSWORD is required", file=sys.stderr)
        return 2

    expected_code = get_agent_code_version()
    print(f"control_plane_code_version={expected_code!r}")

    client = APIClient(
        os.getenv("STP_BACKEND_URL", "http://127.0.0.1:8000"),
        default_smoke_origin(),
        timeout=180.0,
    )
    client.login(os.getenv("STP_ADMIN_USER", "stp-admin"), password)

    hosts = _list_hosts(client)
    online = [h for h in hosts if h.get("status") == "ONLINE"]
    print(f"online_hosts={len(online)}")

    results: list[dict] = []
    deferred: list[dict] = []

    def _hot_update(host: dict, *, abort: bool = False) -> dict:
        host_id = host["id"]
        hostname = host.get("hostname") or host.get("ip") or host_id
        params = {}
        if abort:
            params["abort_running_jobs"] = "true"
        started = time.time()
        resp = client.post(f"/api/v1/hosts/{host_id}/hot-update", params=params or None)
        elapsed_ms = int((time.time() - started) * 1000)
        row = {
            "host_id": host_id,
            "hostname": hostname,
            "status_code": resp.status_code,
            "elapsed_ms": elapsed_ms,
        }
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:500]}
        row["body"] = body
        if resp.status_code == 200:
            data = _unwrap(body) if isinstance(body, dict) else body
            row["ok"] = True
            row["code_version"] = (data or {}).get("code_version")
            row["deps_refreshed"] = (data or {}).get("deps_refreshed")
            print(
                f"  OK {hostname} code_version={row.get('code_version')} "
                f"deps_refreshed={row.get('deps_refreshed')} duration={elapsed_ms}ms"
            )
        else:
            row["ok"] = False
            detail = body.get("detail") if isinstance(body, dict) else body
            if isinstance(detail, dict):
                row["code"] = detail.get("code")
                row["retry_after_seconds"] = detail.get("retry_after_seconds")
            print(f"  FAIL {hostname} status={resp.status_code} detail={detail}")
        return row

    for host in online:
        active_jobs = host.get("active_jobs") or []
        if active_jobs and not args.include_active:
            print(
                f"  SKIP {host.get('hostname')} active_jobs={len(active_jobs)}"
            )
            results.append({
                "host_id": host["id"],
                "hostname": host.get("hostname"),
                "ok": None,
                "skipped": "active_jobs",
                "active_jobs": len(active_jobs),
            })
            continue

        print(f"\n=== hot-update {host.get('hostname')} ({host['id']}) ===")
        row = _hot_update(host, abort=args.abort_running_jobs)
        results.append(row)
        if (
            not row.get("ok")
            and row.get("code") == "HOST_ABORT_PENDING"
            and args.retry_abort_pending
        ):
            wait = int(row.get("retry_after_seconds") or 60) + 2
            print(f"  retry after {wait}s (HOST_ABORT_PENDING)")
            deferred.append((host, wait))

    for host, wait in deferred:
        time.sleep(wait)
        print(f"\n=== retry hot-update {host.get('hostname')} ===")
        results.append(_hot_update(host))

    ok = sum(1 for r in results if r.get("ok") is True)
    fail = sum(1 for r in results if r.get("ok") is False)
    skipped = sum(1 for r in results if r.get("skipped"))
    print(
        f"\nSUMMARY ok={ok} fail={fail} skipped={skipped} "
        f"expected_code_version={expected_code!r}"
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
