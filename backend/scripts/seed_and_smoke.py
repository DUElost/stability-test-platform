"""ADR-0020 dispatcher / plans API 烟雾测试。

真实环境：10.36 节点（host_id=auto-fdaf1d55e319）+ 一台 ONLINE 设备。

流程：
    1. 通过 token 入口登录 admin，拿 Bearer token
    2. 热更新 Agent 代码（同步本地代码到 10.36，可 --no-hot-update 跳过）
    3. 同名 Plan 已存在则 DELETE 重建
    4. 创建 Plan（4 步：init x2 / patrol x1 / teardown x1）
    5. 调 preview 端点，断言 lifecycle 形态
    6. 触发 PlanRun
    7. 轮询直到终态（默认超时 600s，可 --no-wait 跳过）
    8. 输出 PlanRun + JobInstance + StepTrace 摘要

使用示例：
    set STP_ADMIN_PASSWORD=<your-password>
    python backend/scripts/seed_and_smoke.py
    python backend/scripts/seed_and_smoke.py --no-hot-update --no-wait
    python backend/scripts/seed_and_smoke.py --device-id 2429 --timeout 900
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Optional

import httpx


# ── 默认配置 ────────────────────────────────────────────────────────────────

DEFAULT_BACKEND = "http://localhost:8000"
DEFAULT_HOST_ID = "auto-fdaf1d55e319"   # 10.36 节点
DEFAULT_DEVICE_ID = 2429                 # Infinix_X6851 (ONLINE)
DEFAULT_PLAN_NAME = "smoke-plan-001"

PLAN_PAYLOAD: dict[str, Any] = {
    "name": DEFAULT_PLAN_NAME,
    "description": "ADR-0020 dispatcher 烟雾测试 — 真实 10.36 设备",
    "failure_threshold": 0.05,
    # ADR-0020 §2：lifecycle 由 dispatcher 从 PlanStep 行 + 下面两个直列字段实时装配，
    # PlanCreate 已开启 extra="forbid"，请求体中不能再带 lifecycle 字段。
    "patrol_interval_seconds": 60,
    "timeout_seconds": 300,
    "watcher_policy": None,
    "next_plan_id": None,
    "steps": [
        {"step_key": "init_root",       "script_name": "ensure_root",  "script_version": "1.0.0",
         "stage": "init",     "sort_order": 0, "timeout_seconds": 30,  "retry": 0},
        {"step_key": "init_check",      "script_name": "check_device", "script_version": "1.0.0",
         "stage": "init",     "sort_order": 1, "timeout_seconds": 30,  "retry": 0},
        {"step_key": "patrol_check",    "script_name": "check_device", "script_version": "1.0.0",
         "stage": "patrol",   "sort_order": 0, "timeout_seconds": 30,  "retry": 0},
        {"step_key": "teardown_clean",  "script_name": "clean_env",    "script_version": "1.0.0",
         "stage": "teardown", "sort_order": 0, "timeout_seconds": 60,  "retry": 0},
    ],
}

TERMINAL_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "DEGRADED"}


# ── 输出工具 ────────────────────────────────────────────────────────────────

def step(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def info(msg: str) -> None:
    print(f"  {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}", flush=True)


def die(msg: str) -> None:
    print(f"  [FAIL] {msg}", file=sys.stderr, flush=True)
    sys.exit(1)


def _unwrap(body: Any) -> Any:
    """ApiResponse[T] -> T。  若是 {data, error} 包装则解包。"""
    if isinstance(body, dict) and "data" in body and "error" in body:
        return body["data"]
    return body


# ── HTTP 客户端封装 ─────────────────────────────────────────────────────────

class APIClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._token: Optional[str] = None

    def login(self, username: str, password: str) -> None:
        r = self._client.post(
            "/api/v1/auth/token",
            data={"username": username, "password": password},
        )
        if r.status_code != 200:
            die(f"login status={r.status_code} body={r.text[:300]}")
        body = r.json()
        data = _unwrap(body) if isinstance(body, dict) else body
        token = (
            (data.get("access_token") if isinstance(data, dict) else None)
            or (body.get("access_token") if isinstance(body, dict) else None)
        )
        if not token:
            die(f"login response missing access_token: {body}")
        self._token = token

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def post(self, path: str, **kw: Any) -> httpx.Response:
        return self._client.post(path, headers=self._h(), **kw)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self._client.get(path, headers=self._h(), **kw)

    def put(self, path: str, **kw: Any) -> httpx.Response:
        return self._client.put(path, headers=self._h(), **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        return self._client.delete(path, headers=self._h(), **kw)

    def close(self) -> None:
        self._client.close()


# ── 业务步骤 ────────────────────────────────────────────────────────────────

def hot_update(client: APIClient, host_id: str) -> bool:
    step(f"热更新 Agent 代码 → host_id={host_id}")
    r = client.post(f"/api/v1/hosts/{host_id}/hot-update")
    if r.status_code != 200:
        warn(f"hot-update 失败 status={r.status_code} body={r.text[:300]}")
        warn("继续，但 10.36 节点的 Agent 代码可能不是最新的。")
        warn("（如果是 SSH 凭据缺失，请先在 Host 配置里设置 ssh_password/ssh_key_path 或加 Ansible inventory；")
        warn(" 或者用 --no-hot-update 跳过此步）")
        return False
    body = r.json()
    duration = body.get("duration_ms") if isinstance(body, dict) else None
    info(f"hot-update OK duration={duration}ms")
    return True


def cleanup_existing_plan(client: APIClient, plan_name: str) -> None:
    step(f"清理同名旧 Plan name={plan_name}")
    r = client.get("/api/v1/plans?limit=200")
    if r.status_code != 200:
        die(f"list plans status={r.status_code} body={r.text[:300]}")
    plans = _unwrap(r.json())
    if not isinstance(plans, list):
        die(f"list plans 返回非列表: {plans}")
    matched = [p for p in plans if p.get("name") == plan_name]
    if not matched:
        info("无同名旧 Plan")
        return
    for p in matched:
        pid = p["id"]
        d = client.delete(f"/api/v1/plans/{pid}")
        if d.status_code not in (200, 204):
            die(f"DELETE plan {pid} 失败 status={d.status_code} body={d.text[:300]}")
        info(f"已删除旧 Plan id={pid}")


def create_plan(client: APIClient) -> int:
    step("创建 Plan")
    r = client.post("/api/v1/plans", json=PLAN_PAYLOAD)
    if r.status_code != 201:
        die(f"create_plan status={r.status_code} body={r.text[:500]}")
    plan = _unwrap(r.json())
    plan_id = plan["id"]
    info(f"plan_id={plan_id} name={plan['name']} steps_count={len(plan.get('steps', []))}")
    return plan_id


def preview(client: APIClient, plan_id: int, device_ids: list[int]) -> None:
    step(f"预览扇出 plan_id={plan_id} device_ids={device_ids}")
    r = client.post(
        f"/api/v1/plans/{plan_id}/run/preview",
        json={"device_ids": device_ids},
    )
    if r.status_code != 200:
        die(f"preview status={r.status_code} body={r.text[:500]}")
    pv = _unwrap(r.json())
    info(f"device_count={pv.get('device_count')} job_count={pv.get('job_count')} "
         f"total_steps={pv.get('total_steps')}")
    lc = pv.get("lifecycle", {}) or {}
    init_cnt = len(lc.get("init") or [])
    tear_cnt = len(lc.get("teardown") or [])
    patrol = lc.get("patrol") or {}
    pat_cnt = len(patrol.get("steps") or [])
    info(f"lifecycle.init={init_cnt} steps  patrol.steps={pat_cnt} "
         f"interval={patrol.get('interval_seconds')}  teardown={tear_cnt} steps  "
         f"timeout_seconds={lc.get('timeout_seconds')}")
    # 断言
    if pv.get("device_count") != len(device_ids):
        die(f"device_count mismatch: expected {len(device_ids)} got {pv.get('device_count')}")
    if pv.get("total_steps") != 4:
        die(f"total_steps mismatch: expected 4 got {pv.get('total_steps')}")
    if init_cnt != 2 or pat_cnt != 1 or tear_cnt != 1:
        die(f"lifecycle 分布异常 init={init_cnt} patrol={pat_cnt} teardown={tear_cnt}")
    info("preview 断言通过")


def trigger(client: APIClient, plan_id: int, device_ids: list[int]) -> int:
    step(f"触发 PlanRun plan_id={plan_id} device_ids={device_ids}")
    r = client.post(f"/api/v1/plans/{plan_id}/run", json={"device_ids": device_ids})
    if r.status_code != 200:
        die(f"trigger status={r.status_code} body={r.text[:500]}")
    pr = _unwrap(r.json())
    info(f"plan_run_id={pr['id']} status={pr['status']} run_type={pr.get('run_type')}")
    return pr["id"]


def poll(client: APIClient, plan_run_id: int, timeout_sec: int, interval_sec: int = 5) -> Optional[dict]:
    step(f"轮询 PlanRun {plan_run_id}（最长 {timeout_sec}s，每 {interval_sec}s 一次）")
    deadline = time.time() + timeout_sec
    last_status: Optional[str] = None
    last_jobs_signature: Optional[str] = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/plan-runs/{plan_run_id}")
        if r.status_code != 200:
            warn(f"poll status={r.status_code}, retry...")
            time.sleep(interval_sec)
            continue
        pr = _unwrap(r.json())
        status = pr.get("status")
        jobs = pr.get("jobs") or []
        sig = ",".join(f"j{j['id']}={j.get('status')}" for j in jobs)
        if status != last_status or sig != last_jobs_signature:
            ts = datetime.now().strftime("%H:%M:%S")
            info(f"[{ts}] PlanRun.status={status}  jobs=[{sig}]")
            last_status = status
            last_jobs_signature = sig
        if status in TERMINAL_STATUSES:
            return pr
        time.sleep(interval_sec)
    warn(f"轮询超时（{timeout_sec}s 内未到达终态）")
    r = client.get(f"/api/v1/plan-runs/{plan_run_id}")
    return _unwrap(r.json()) if r.status_code == 200 else None


def report(plan_run: dict) -> None:
    step("最终报告")
    info(f"PlanRun id={plan_run['id']} status={plan_run.get('status')}")
    info(f"  started={plan_run.get('started_at')} ended={plan_run.get('ended_at')}")
    rs = plan_run.get("result_summary")
    if rs:
        info(f"  result_summary={json.dumps(rs, ensure_ascii=False)}")
    for job in plan_run.get("jobs") or []:
        info(f"  Job {job['id']}: device={job.get('device_id')} "
             f"status={job.get('status')} "
             f"started={job.get('started_at')} ended={job.get('ended_at')}")
        if job.get("status_reason"):
            info(f"    reason={job['status_reason']}")
        traces = job.get("step_traces") or []
        if not traces:
            info("    (no step_traces)")
        for t in traces[:8]:
            err = f" err={t['error_message'][:80]}" if t.get("error_message") else ""
            info(f"    [{t.get('original_ts','')}] step={t.get('step_id')}({t.get('stage')}) "
                 f"{t.get('event_type')}/{t.get('status')}{err}")
        if len(traces) > 8:
            info(f"    ... ({len(traces) - 8} more step_traces)")


# ── 入口 ────────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", default=DEFAULT_BACKEND,
                   help=f"后端地址（default: {DEFAULT_BACKEND}）")
    p.add_argument("--username", default=os.getenv("STP_ADMIN_USER", "admin"),
                   help="登录用户名（env: STP_ADMIN_USER）")
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"),
                   help="登录密码（env: STP_ADMIN_PASSWORD, required unless set in env）")
    p.add_argument("--target-host-id", default=DEFAULT_HOST_ID,
                   help=f"热更新目标 host_id（default: {DEFAULT_HOST_ID}）")
    p.add_argument("--device-id", type=int, default=DEFAULT_DEVICE_ID,
                   help=f"用于 dispatch 的 device_id（default: {DEFAULT_DEVICE_ID}）")
    p.add_argument("--no-hot-update", action="store_true",
                   help="跳过 Agent 热更新")
    p.add_argument("--no-wait", action="store_true",
                   help="触发后立即退出，不等待 PlanRun 终态")
    p.add_argument("--timeout", type=int, default=600,
                   help="轮询超时秒数（default: 600）")
    p.add_argument("--poll-interval", type=int, default=5,
                   help="轮询间隔秒数（default: 5）")
    args = p.parse_args()

    if not args.password:
        die("Missing admin password: set STP_ADMIN_PASSWORD or pass --password explicitly.")

    client = APIClient(args.backend)
    try:
        step(f"登录 {args.backend} as {args.username}")
        client.login(args.username, args.password)
        info("login OK")

        if not args.no_hot_update:
            hot_update(client, args.target_host_id)

        cleanup_existing_plan(client, DEFAULT_PLAN_NAME)
        plan_id = create_plan(client)
        preview(client, plan_id, [args.device_id])
        plan_run_id = trigger(client, plan_id, [args.device_id])

        if args.no_wait:
            step("--no-wait 模式")
            info(f"plan_id={plan_id}  plan_run_id={plan_run_id}")
            info(f"后续查询：")
            info(f"  GET {args.backend}/api/v1/plan-runs/{plan_run_id}")
            info(f"  GET {args.backend}/api/v1/plan-runs/{plan_run_id}/jobs")
            info(f"  GET {args.backend}/api/v1/plan-runs/{plan_run_id}/summary")
            return

        pr = poll(client, plan_run_id,
                  timeout_sec=args.timeout, interval_sec=args.poll_interval)
        if pr is not None:
            report(pr)
        else:
            warn("无法获取 PlanRun 详情")
    finally:
        client.close()


if __name__ == "__main__":
    main()
