"""Sprint 4 真机验收：10.36 三设备 PlanRun + dedup 管道。

基于 seed_and_smoke.py，扩展为：
  - 指定 host（默认 auto-fdaf1d55e319 / 172.21.10.36）下全部 ONLINE 设备扇出
  - 主链 smoke → watcher-summary → dedup scan/merge 状态轮询

示例：
    set STP_ADMIN_PASSWORD=<password>
    set STP_SMOKE_ORIGIN=http://172.21.10.25:5173
    python backend/scripts/sprint4_real_device_verify.py
    python backend/scripts/sprint4_real_device_verify.py --no-hot-update --patrol-interval 30
    python backend/scripts/sprint4_real_device_verify.py --skip-run --plan-run-id 49
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Optional

# Reuse seed_and_smoke helpers (same directory).
from seed_and_smoke import (  # noqa: E402
    APIClient,
    DEFAULT_BACKEND,
    DEFAULT_PLAN_NAME,
    DEFAULT_SMOKE_ORIGIN,
    PASSING_STATUSES,
    PLAN_PAYLOAD,
    TERMINAL_STATUSES,
    _unwrap,
    default_smoke_origin,
    die,
    ensure_smoke_plan,
    fetch_devices,
    fetch_hosts,
    hot_update,
    info,
    load_repo_dotenv,
    poll,
    preview,
    report,
    step,
    trigger,
    warn,
)

DEFAULT_HOST_ID = "auto-fdaf1d55e319"
DEFAULT_HOST_IP = "172.21.10.36"
PROD_BACKEND = "http://172.21.10.25:8000"


def resolve_host_devices(
    client: APIClient,
    host_id: str,
    *,
    min_devices: int = 1,
    max_devices: Optional[int] = None,
) -> list[int]:
    devices = fetch_devices(client)
    hosts = fetch_hosts(client)
    host = next((h for h in hosts if h.get("id") == host_id), None)
    if host is None:
        die(f"host_id={host_id!r} 不存在")
    if host.get("status") != "ONLINE":
        die(f"host_id={host_id!r} 状态={host.get('status')!r}，期望 ONLINE")

    on_host = [
        d for d in devices
        if d.get("host_id") == host_id and d.get("status") == "ONLINE"
    ]
    if len(on_host) < min_devices:
        die(
            f"host {host_id} ({host.get('ip')}) 仅 {len(on_host)} 台 ONLINE 设备，"
            f"至少需要 {min_devices} 台"
        )
    if max_devices is not None:
        on_host = on_host[:max_devices]

    ids = [int(d["id"]) for d in on_host]
    serials = [d.get("serial", "?") for d in on_host]
    info(f"host {host_id} ip={host.get('ip')} status=ONLINE")
    for did, serial in zip(ids, serials):
        info(f"  device_id={did} serial={serial}")
    return ids


def patch_plan_patrol_interval(client: APIClient, plan_id: int, interval: int) -> None:
    payload = {**PLAN_PAYLOAD, "name": DEFAULT_PLAN_NAME, "patrol_interval_seconds": interval}
    r = client.put(f"/api/v1/plans/{plan_id}", json=payload)
    if r.status_code != 200:
        warn(f"更新 patrol_interval 失败 status={r.status_code}，继续使用 Plan 默认值")
    else:
        info(f"patrol_interval_seconds={interval}")


def fetch_watcher_summary(client: APIClient, plan_run_id: int) -> dict[str, Any]:
    r = client.get(f"/api/v1/plan-runs/{plan_run_id}/watcher-summary")
    if r.status_code != 200:
        die(f"watcher-summary status={r.status_code} body={r.text[:400]}")
    return _unwrap(r.json())


def fetch_dedup_status(client: APIClient, plan_run_id: int) -> dict[str, Any]:
    r = client.get(f"/api/v1/plan-runs/{plan_run_id}/dedup/status")
    if r.status_code != 200:
        die(f"dedup/status status={r.status_code} body={r.text[:400]}")
    return _unwrap(r.json())


def trigger_dedup_scan(client: APIClient, plan_run_id: int, *, is_final: bool = True) -> dict[str, Any]:
    r = client.post(
        f"/api/v1/plan-runs/{plan_run_id}/dedup/scan",
        params={"is_final": str(is_final).lower()},
    )
    if r.status_code != 200:
        die(f"dedup/scan status={r.status_code} body={r.text[:400]}")
    return _unwrap(r.json())


def trigger_dedup_merge(client: APIClient, plan_run_id: int) -> dict[str, Any]:
    r = client.post(f"/api/v1/plan-runs/{plan_run_id}/dedup/merge")
    if r.status_code != 200:
        die(f"dedup/merge status={r.status_code} body={r.text[:400]}")
    return _unwrap(r.json())


def poll_dedup_artifacts(
    client: APIClient,
    plan_run_id: int,
    *,
    want_types: set[str],
    timeout_sec: int = 600,
    interval_sec: int = 10,
) -> dict[str, Any]:
    step(f"轮询 dedup 产物 plan_run={plan_run_id} want={sorted(want_types)}")
    deadline = time.time() + timeout_sec
    last_types: set[str] = set()
    while time.time() < deadline:
        status = fetch_dedup_status(client, plan_run_id)
        artifacts = status.get("artifacts") or []
        found = {a.get("artifact_type") for a in artifacts if a.get("artifact_type")}
        if found != last_types:
            ts = datetime.now().strftime("%H:%M:%S")
            info(f"[{ts}] artifact_types={sorted(found)} count={len(artifacts)}")
            last_types = found
        if want_types.issubset(found):
            return status
        time.sleep(interval_sec)
    warn(f"dedup 轮询超时 {timeout_sec}s，当前 types={sorted(last_types)}")
    return fetch_dedup_status(client, plan_run_id)


def print_verification_summary(
    plan_run: dict[str, Any],
    watcher: dict[str, Any],
    dedup: dict[str, Any],
) -> None:
    step("验收摘要（填 #30 / docs/acceptance/2026-plan-c-sprint4-real-device.md）")
    pr_id = plan_run.get("id")
    jobs = plan_run.get("jobs") or []
    info(f"PlanRun id={pr_id} status={plan_run.get('status')} jobs={len(jobs)}")
    for j in jobs:
        info(f"  Job {j.get('id')}: device={j.get('device_id')} status={j.get('status')}")

    archive = watcher.get("archive") or {}
    scan_status = archive.get("scan_status")
    info(f"watcher-summary.archive.scan_status={scan_status!r}")
    risk = watcher.get("risk_summary") or {}
    if risk:
        info(f"risk_summary={json.dumps(risk, ensure_ascii=False)[:200]}")

    artifacts = dedup.get("artifacts") or []
    by_type: dict[str, int] = {}
    for a in artifacts:
        t = a.get("artifact_type") or "unknown"
        by_type[t] = by_type.get(t, 0) + 1
    info(f"dedup artifacts total={len(artifacts)} by_type={by_type}")
    for a in artifacts[:6]:
        info(
            f"  [{a.get('artifact_type')}] host={a.get('host_id')} "
            f"uri={a.get('storage_uri')} size={a.get('size_bytes')}"
        )

    checks = {
        "PlanRun 终态 SUCCESS/PARTIAL_SUCCESS": plan_run.get("status") in PASSING_STATUSES,
        "Job 数 = 设备数": len(jobs) == len({j.get("device_id") for j in jobs}),
        "scan_status 非 pending": scan_status in ("scanned", "merged"),
        "存在 scan_result_xls": by_type.get("scan_result_xls", 0) >= 1,
        "存在 merge_result_xls": by_type.get("merge_result_xls", 0) >= 1,
    }
    step("自动判定")
    all_pass = True
    for label, ok in checks.items():
        mark = "PASS" if ok else "FAIL"
        info(f"  [{mark}] {label}")
        all_pass = all_pass and ok
    if all_pass:
        info(">>> 核心管道验收通过，可补签字表并关 #30 <<<")
    else:
        warn(">>> 有 FAIL 项，请查 Agent 日志 / NFS dedup/ 目录 <<<")


def main() -> None:
    env_file = load_repo_dotenv()

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", default=os.getenv("STP_VERIFY_BACKEND", PROD_BACKEND))
    p.add_argument("--username", default=os.getenv("STP_ADMIN_USER", "admin"))
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"))
    p.add_argument("--origin", default=default_smoke_origin())
    p.add_argument("--host-id", default=DEFAULT_HOST_ID)
    p.add_argument("--min-devices", type=int, default=1)
    p.add_argument("--max-devices", type=int, default=None)
    p.add_argument("--patrol-interval", type=int, default=60,
                   help="缩短 patrol 周期加速验收（秒）")
    p.add_argument("--no-hot-update", action="store_true")
    p.add_argument("--timeout", type=int, default=900, help="PlanRun 轮询超时")
    p.add_argument("--dedup-timeout", type=int, default=600, help="dedup 产物轮询超时")
    p.add_argument("--skip-run", action="store_true",
                   help="跳过 PlanRun，仅对已有 run 做 dedup 验收")
    p.add_argument("--plan-run-id", type=int, default=None,
                   help="与 --skip-run 联用，或失败后重试 dedup")
    p.add_argument("--no-merge", action="store_true", help="不手动触发 merge（等 SAQ 自动）")
    args = p.parse_args()

    if not args.password:
        die(
            "需要 STP_ADMIN_PASSWORD 或 --password。"
            f"（checked {env_file}）"
        )

    client = APIClient(args.backend, origin=args.origin)
    plan_run_id: Optional[int] = args.plan_run_id

    try:
        step(f"登录 {args.backend}")
        client.login(args.username, args.password, env_file=env_file)
        info("login OK")

        device_ids = resolve_host_devices(
            client, args.host_id,
            min_devices=args.min_devices,
            max_devices=args.max_devices,
        )

        if not args.no_hot_update:
            hot_update(client, args.host_id)
            reload = client.post(f"/api/v1/plan-runs/hosts/{args.host_id}/reload-config")
            if reload.status_code == 200:
                info("reload-config sent")
            else:
                warn(f"reload-config status={reload.status_code}")

        if not args.skip_run:
            plan_id = ensure_smoke_plan(client, DEFAULT_PLAN_NAME)
            if args.patrol_interval != 60:
                patch_plan_patrol_interval(client, plan_id, args.patrol_interval)
            preview(client, plan_id, device_ids)
            plan_run_id = trigger(client, plan_id, device_ids)
            pr = poll(client, plan_run_id, timeout_sec=args.timeout)
            if pr is None:
                die("无法获取 PlanRun 详情")
            report(pr)
            if pr.get("status") not in PASSING_STATUSES:
                die(f"PlanRun 终态={pr.get('status')}，主链 smoke 失败")
            plan_run = pr
        else:
            if plan_run_id is None:
                die("--skip-run 需要 --plan-run-id")
            r = client.get(f"/api/v1/plan-runs/{plan_run_id}")
            if r.status_code != 200:
                die(f"get plan run status={r.status_code}")
            plan_run = _unwrap(r.json())
            if plan_run.get("status") not in TERMINAL_STATUSES:
                warn(f"PlanRun {plan_run_id} 尚未终态: {plan_run.get('status')}")

        step("watcher-summary")
        watcher = fetch_watcher_summary(client, plan_run_id)
        archive = watcher.get("archive") or {}
        info(f"scan_status={archive.get('scan_status')!r} pending_jobs={archive.get('pending_jobs')}")

        dedup = fetch_dedup_status(client, plan_run_id)
        types = {a.get("artifact_type") for a in (dedup.get("artifacts") or [])}
        if "scan_result_xls" not in types:
            step("手动触发 dedup/scan（终态自动可能仍在队列中）")
            trig = trigger_dedup_scan(client, plan_run_id, is_final=True)
            info(f"triggered_hosts={trig.get('triggered_hosts')}")
            dedup = poll_dedup_artifacts(
                client, plan_run_id,
                want_types={"scan_result_xls"},
                timeout_sec=args.dedup_timeout,
            )

        types = {a.get("artifact_type") for a in (dedup.get("artifacts") or [])}
        if "merge_result_xls" not in types and not args.no_merge:
            step("触发 dedup/merge")
            trigger_dedup_merge(client, plan_run_id)
            dedup = poll_dedup_artifacts(
                client, plan_run_id,
                want_types={"merge_result_xls"},
                timeout_sec=args.dedup_timeout,
            )

        watcher = fetch_watcher_summary(client, plan_run_id)
        print_verification_summary(plan_run, watcher, dedup)
    finally:
        client.close()


if __name__ == "__main__":
    main()
