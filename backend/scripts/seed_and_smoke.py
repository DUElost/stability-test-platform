"""ADR-0020 dispatcher / plans API 烟雾测试。

真实环境：10.36 节点（host_id=auto-fdaf1d55e319）+ 一台 ONLINE 设备。

流程：
    1. 通过 cookie 会话登录 admin（POST /auth/login + Origin，与浏览器一致）
    2. 热更新 Agent 代码（同步本地代码到 10.36，可 --no-hot-update 跳过）
    3. 同名 Plan 已存在则 DELETE 重建
    4. 创建 Plan（4 步：init x2 / patrol x1 / teardown x1）
    5. 调 preview 端点，断言 lifecycle 形态
    6. 触发 PlanRun
    7. 轮询直到终态（默认超时 600s，可 --no-wait 跳过）
    8. 输出 PlanRun + JobInstance + StepTrace 摘要

使用示例：
    # 根目录 .env 中设置 STP_ADMIN_PASSWORD（及可选 STP_ADMIN_USER）即可
    set STP_ADMIN_PASSWORD=<your-password>
    set STP_ADMIN_USER=admin
    set STP_SMOKE_ORIGIN=http://localhost:5173
    # 开发库若无 admin 或密码不一致，先执行（DEV ONLY）：
    python backend/scripts/reset_dev_admin_password.py
    python backend/scripts/seed_and_smoke.py
    python backend/scripts/seed_and_smoke.py --no-hot-update --no-wait
    python backend/scripts/seed_and_smoke.py --device-id 2429 --timeout 900
    # 开发库可省略 --device-id / --target-host-id，登录后自动选 ONLINE 设备及其 host
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import httpx


# ── 仓库根 .env 加载 ────────────────────────────────────────────────────────

def find_repo_root(start: Optional[Path] = None) -> Path:
    """向上查找仓库根（.git 或 pyproject.toml）；否则回退到 backend/scripts 上两级。"""
    current = (start or Path(__file__).resolve().parent).resolve()
    for path in (current, *current.parents):
        if (path / ".git").exists() or (path / "pyproject.toml").exists():
            return path
    return Path(__file__).resolve().parents[2]


def _parse_dotenv_simple(path: Path, environ: dict[str, str]) -> None:
    """简单 KEY=VALUE 解析；不覆盖 environ 中已存在的键。"""
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or key in environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        environ[key] = value


def load_repo_dotenv(repo_root: Optional[Path] = None) -> Path:
    """从仓库根加载 .env（override=False）。返回 .env 路径（文件可能不存在）。"""
    root = repo_root.resolve() if repo_root is not None else find_repo_root()
    env_file = root / ".env"
    if not env_file.is_file():
        return env_file
    try:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)
    except ImportError:
        _parse_dotenv_simple(env_file, os.environ)
    return env_file


# ── 默认配置 ────────────────────────────────────────────────────────────────

DEFAULT_BACKEND = "http://localhost:8000"
DEFAULT_SMOKE_ORIGIN = "http://localhost:5173"
# 预发布/生产可显式指定；开发库省略时由 resolve_smoke_targets 自动探测
DEFAULT_HOST_ID: Optional[str] = None
DEFAULT_DEVICE_ID: Optional[int] = None
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
PASSING_STATUSES = {"SUCCESS", "PARTIAL_SUCCESS"}


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


def default_smoke_origin() -> str:
    """CSRF 白名单 Origin，可通过 STP_SMOKE_ORIGIN 覆盖。"""
    return os.getenv("STP_SMOKE_ORIGIN", DEFAULT_SMOKE_ORIGIN).strip() or DEFAULT_SMOKE_ORIGIN


def build_csrf_headers(origin: str) -> dict[str, str]:
    """构造 CSRF 中间件兼容的 Origin/Referer（cookie 会话写操作必需）。"""
    base = origin.rstrip("/")
    return {"Origin": base, "Referer": f"{base}/"}


def login_failure_hint(
    *,
    status_code: int,
    body: str,
    username: str,
    env_file: Optional[Path] = None,
) -> str:
    """Actionable dev hint for common cookie-login failures (no secrets)."""
    if status_code != 401 or "Incorrect username or password" not in body:
        return ""
    env_user = os.getenv("STP_ADMIN_USER")
    lines = [
        "",
        "  Dev hint: POST /auth/login uses OAuth2 username (not email).",
        f"  Attempted username={username!r}.",
    ]
    if env_user and env_user != username:
        lines.append(
            f"  STP_ADMIN_USER={env_user!r} is set but was not used "
            "(pass --username or unset shell override)."
        )
    elif not env_user:
        lines.append(
            "  No STP_ADMIN_USER in .env — default is 'admin'. "
            "Set STP_ADMIN_USER if your DB admin uses another username."
        )
    lines.extend(
        [
            "  Ensure the DB user password matches STP_ADMIN_PASSWORD from .env.",
            "  Reset/create dev admin (DEV ONLY):",
            "    python backend/scripts/reset_dev_admin_password.py",
        ]
    )
    if env_file is not None:
        lines.append(f"  (.env checked: {env_file})")
    return "\n".join(lines)


# ── HTTP 客户端封装 ─────────────────────────────────────────────────────────

class APIClient:
    def __init__(self, base_url: str, origin: str, timeout: float = 30.0):
        self._origin = origin
        # Avoid implicit proxies from environment variables in preprod hosts.
        self._client = httpx.Client(base_url=base_url, timeout=timeout, trust_env=False)
        self._logged_in = False

    def login(self, username: str, password: str, *, env_file: Optional[Path] = None) -> None:
        r = self._client.post(
            "/api/v1/auth/login",
            data={"username": username, "password": password},
            headers=build_csrf_headers(self._origin),
        )
        if r.status_code != 200:
            hint = login_failure_hint(
                status_code=r.status_code,
                body=r.text,
                username=username,
                env_file=env_file,
            )
            die(f"login status={r.status_code} body={r.text[:300]}{hint}")
        body = r.json()
        data = _unwrap(body) if isinstance(body, dict) else body
        if isinstance(data, dict) and data.get("ok") is not True:
            die(f"login response unexpected: {body}")
        me = self._client.get("/api/v1/auth/me")
        if me.status_code != 200:
            die(f"login session verify failed status={me.status_code} body={me.text[:300]}")
        self._logged_in = True

    def _h(self, *, csrf: bool = False) -> dict[str, str]:
        if csrf:
            return build_csrf_headers(self._origin)
        return {}

    def _merge_headers(self, extra: Optional[dict[str, str]], *, csrf: bool) -> dict[str, str]:
        merged = self._h(csrf=csrf)
        if extra:
            merged.update(extra)
        return merged

    def post(self, path: str, **kw: Any) -> httpx.Response:
        headers = self._merge_headers(kw.pop("headers", None), csrf=True)
        return self._client.post(path, headers=headers, **kw)

    def get(self, path: str, **kw: Any) -> httpx.Response:
        headers = self._merge_headers(kw.pop("headers", None), csrf=False)
        return self._client.get(path, headers=headers, **kw)

    def put(self, path: str, **kw: Any) -> httpx.Response:
        headers = self._merge_headers(kw.pop("headers", None), csrf=True)
        return self._client.put(path, headers=headers, **kw)

    def delete(self, path: str, **kw: Any) -> httpx.Response:
        headers = self._merge_headers(kw.pop("headers", None), csrf=True)
        return self._client.delete(path, headers=headers, **kw)

    def close(self) -> None:
        self._client.close()


# ── 业务步骤 ────────────────────────────────────────────────────────────────

def fetch_devices(client: APIClient, *, limit: int = 500) -> list[dict[str, Any]]:
    """GET /api/v1/devices，兼容数组与 PaginatedResponse。"""
    r = client.get("/api/v1/devices", params={"limit": limit})
    if r.status_code != 200:
        die(f"list devices status={r.status_code} body={r.text[:300]}")
    body = _unwrap(r.json())
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    die(f"list devices 返回非预期结构: {body!r}")


def fetch_hosts(client: APIClient, *, limit: int = 200) -> list[dict[str, Any]]:
    """GET /api/v1/hosts，兼容数组与 PaginatedResponse。"""
    r = client.get("/api/v1/hosts", params={"limit": limit})
    if r.status_code != 200:
        die(f"list hosts status={r.status_code} body={r.text[:300]}")
    body = _unwrap(r.json())
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("items"), list):
        return body["items"]
    die(f"list hosts 返回非预期结构: {body!r}")


def _online_devices_with_host(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        d
        for d in devices
        if d.get("status") == "ONLINE" and d.get("host_id")
    ]


def _sample_device_ids(devices: list[dict[str, Any]], n: int = 8) -> list[int]:
    return [int(d["id"]) for d in devices[:n] if d.get("id") is not None]


def _no_device_hint(devices: list[dict[str, Any]]) -> str:
    sample = _sample_device_ids(devices)
    lines = [
        "无可用 ONLINE 设备（需 status=ONLINE 且已关联 host）。",
        "请注册设备并启动 Agent 心跳后再试。",
    ]
    if sample:
        lines.append(f"本库部分 device id 供参考: {sample}")
    elif devices:
        lines.append(
            f"本库有 {len(devices)} 台设备但均非 ONLINE 或未关联 host。"
        )
    else:
        lines.append("本库当前无任何设备记录。")
    return " ".join(lines)


def resolve_smoke_targets(
    client: APIClient,
    *,
    device_id: Optional[int],
    target_host_id: Optional[str],
    device_id_explicit: bool,
    target_host_id_explicit: bool,
) -> tuple[int, str]:
    """解析 smoke 用的 device_id 与 target_host_id（preview/trigger 前调用）。"""
    step("解析 smoke 目标 device / host")
    devices = fetch_devices(client)
    device_by_id = {int(d["id"]): d for d in devices if d.get("id") is not None}

    if device_id_explicit:
        if device_id is None:
            die("--device-id 需要正整数")
        if device_id not in device_by_id:
            sample = _sample_device_ids(devices)
            hint = f" sample_device_ids={sample}" if sample else ""
            die(
                f"device_id={device_id} not_found in this database.{hint} "
                "Omit --device-id to auto-select an ONLINE device, or pick an id from the list."
            )
        dev = device_by_id[device_id]
        resolved_host = (
            target_host_id
            if target_host_id_explicit
            else (dev.get("host_id") or target_host_id)
        )
        if not resolved_host:
            die(
                f"device_id={device_id} exists but has no host_id; "
                "assign a host or pass --target-host-id."
            )
        info(
            f"使用显式 device_id={device_id} host_id={resolved_host} "
            f"(serial={dev.get('serial')!r} status={dev.get('status')})"
        )
        return device_id, str(resolved_host)

    candidates = _online_devices_with_host(devices)
    if target_host_id_explicit and target_host_id:
        host_candidates = [d for d in candidates if d.get("host_id") == target_host_id]
        if host_candidates:
            dev = host_candidates[0]
            info(
                f"自动选用 host_id={target_host_id} 下 device_id={dev['id']} "
                f"(serial={dev.get('serial')!r})"
            )
            return int(dev["id"]), str(target_host_id)
        warn(
            f"--target-host-id={target_host_id!r} 下无 ONLINE 设备，"
            "尝试从任意 ONLINE host 选取"
        )

    if candidates:
        dev = candidates[0]
        host = str(dev["host_id"])
        info(
            f"自动选用 device_id={dev['id']} host_id={host} "
            f"(serial={dev.get('serial')!r})"
        )
        return int(dev["id"]), host

    hosts = fetch_hosts(client)
    online_hosts = [h for h in hosts if h.get("status") == "ONLINE"]
    for host in online_hosts:
        hid = host.get("id")
        if not hid:
            continue
        host_devices = [d for d in devices if d.get("host_id") == hid]
        if not host_devices:
            continue
        online_on_host = [d for d in host_devices if d.get("status") == "ONLINE"]
        dev = (online_on_host or host_devices)[0]
        info(
            f"自动选用 ONLINE host_id={hid} 下 device_id={dev['id']} "
            f"(serial={dev.get('serial')!r} status={dev.get('status')})"
        )
        return int(dev["id"]), str(hid)

    die(_no_device_hint(devices))


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


def _update_existing_plan(
    client: APIClient,
    plan_id: int,
    plan_name: str,
    *,
    plan_payload: dict[str, Any],
) -> int:
    """PUT 更新已有 Plan（保留 plan_id，供有执行历史的 Plan 复用）。"""
    payload = {**plan_payload, "name": plan_name}
    r = client.put(f"/api/v1/plans/{plan_id}", json=payload)
    if r.status_code != 200:
        die(f"update plan {plan_id} status={r.status_code} body={r.text[:500]}")
    plan = _unwrap(r.json())
    info(
        f"已更新现有 Plan id={plan['id']} steps_count={len(plan.get('steps', []))}"
    )
    return int(plan["id"])


def ensure_smoke_plan(
    client: APIClient,
    plan_name: str,
    *,
    plan_payload: dict[str, Any],
) -> int:
    """清理或复用同名 smoke Plan：能删则删后新建，有执行历史则 PUT 就地更新。"""
    step(f"确保 smoke Plan name={plan_name}")
    r = client.get("/api/v1/plans?limit=200")
    if r.status_code != 200:
        die(f"list plans status={r.status_code} body={r.text[:300]}")
    plans = _unwrap(r.json())
    if not isinstance(plans, list):
        die(f"list plans 返回非列表: {plans}")
    matched = [p for p in plans if p.get("name") == plan_name]
    if not matched:
        info("无同名旧 Plan，创建新 Plan")
        return create_plan(client, plan_payload=plan_payload)

    reuse_id: Optional[int] = None
    deleted_any = False
    for p in matched:
        pid = p["id"]
        d = client.delete(f"/api/v1/plans/{pid}")
        if d.status_code in (200, 204):
            info(f"已删除旧 Plan id={pid}")
            deleted_any = True
            continue
        if d.status_code == 409:
            if reuse_id is None:
                reuse_id = pid
                warn(
                    f"Plan id={pid} 有执行历史无法 DELETE（409），将 PUT 更新步骤定义"
                )
            else:
                warn(f"跳过额外同名 Plan id={pid}（请先手工清理）")
            continue
        die(f"DELETE plan {pid} 失败 status={d.status_code} body={d.text[:300]}")

    if reuse_id is not None:
        return _update_existing_plan(
            client,
            reuse_id,
            plan_name,
            plan_payload=plan_payload,
        )
    if deleted_any:
        return create_plan(client, plan_payload=plan_payload)
    die(f"同名 Plan 存在但无法删除或更新: name={plan_name}")


def create_plan(client: APIClient, *, plan_payload: dict[str, Any]) -> int:
    step("创建 Plan")
    r = client.post("/api/v1/plans", json=plan_payload)
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
    env_file = load_repo_dotenv()

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", default=DEFAULT_BACKEND,
                   help=f"后端地址（default: {DEFAULT_BACKEND}）")
    p.add_argument("--username", default=os.getenv("STP_ADMIN_USER", "admin"),
                   help="登录用户名（env: STP_ADMIN_USER）")
    p.add_argument("--password", default=os.getenv("STP_ADMIN_PASSWORD"),
                   help="登录密码（env: STP_ADMIN_PASSWORD, required unless set in env）")
    p.add_argument(
        "--target-host-id",
        default=DEFAULT_HOST_ID,
        help="热更新目标 host_id；省略时与自动选中的 device 所属 host 一致",
    )
    p.add_argument(
        "--device-id",
        type=int,
        default=DEFAULT_DEVICE_ID,
        help="用于 dispatch 的 device_id；省略时自动选 status=ONLINE 且已关联 host 的设备",
    )
    p.add_argument("--no-hot-update", action="store_true",
                   help="跳过 Agent 热更新")
    p.add_argument("--no-wait", action="store_true",
                   help="触发后立即退出，不等待 PlanRun 终态")
    p.add_argument(
        "--no-adb",
        action="store_true",
        help="在无 adb/真机环境下使用 noop 脚本跑通 dispatcher 链路（DEV ONLY）",
    )
    p.add_argument("--timeout", type=int, default=600,
                   help="轮询超时秒数（default: 600）")
    p.add_argument("--poll-interval", type=int, default=5,
                   help="轮询间隔秒数（default: 5）")
    p.add_argument("--origin", default=default_smoke_origin(),
                   help=f"CSRF Origin/Referer（default: {DEFAULT_SMOKE_ORIGIN}，env: STP_SMOKE_ORIGIN）")
    args = p.parse_args()
    device_id_explicit = "--device-id" in sys.argv
    target_host_id_explicit = "--target-host-id" in sys.argv

    if not args.password:
        die(
            "Missing admin password: set STP_ADMIN_PASSWORD or pass --password explicitly. "
            f"(checked {env_file})"
        )

    client = APIClient(args.backend, origin=args.origin)
    try:
        step(f"登录 {args.backend} as {args.username}")
        client.login(args.username, args.password, env_file=env_file)
        info("login OK")

        device_id, target_host_id = resolve_smoke_targets(
            client,
            device_id=args.device_id,
            target_host_id=args.target_host_id,
            device_id_explicit=device_id_explicit,
            target_host_id_explicit=target_host_id_explicit,
        )
        info(f"smoke targets: device_id={device_id} host_id={target_host_id}")

        if not args.no_hot_update:
            hot_update(client, target_host_id)

        plan_payload = PLAN_PAYLOAD
        if args.no_adb:
            plan_payload = {**PLAN_PAYLOAD}
            plan_payload["patrol_interval_seconds"] = 2
            plan_payload["timeout_seconds"] = 20
            plan_payload["steps"] = [
                {"step_key": "init_noop_0", "script_name": "noop", "script_version": "1.0.0",
                 "stage": "init", "sort_order": 0, "timeout_seconds": 10, "retry": 0},
                {"step_key": "init_noop_1", "script_name": "noop", "script_version": "1.0.0",
                 "stage": "init", "sort_order": 1, "timeout_seconds": 10, "retry": 0},
                {"step_key": "patrol_noop", "script_name": "noop", "script_version": "1.0.0",
                 "stage": "patrol", "sort_order": 0, "timeout_seconds": 10, "retry": 0},
                {"step_key": "teardown_noop", "script_name": "noop", "script_version": "1.0.0",
                 "stage": "teardown", "sort_order": 0, "timeout_seconds": 10, "retry": 0},
            ]

        plan_id = ensure_smoke_plan(client, DEFAULT_PLAN_NAME, plan_payload=plan_payload)
        preview(client, plan_id, [device_id])
        plan_run_id = trigger(client, plan_id, [device_id])

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
        if pr is None:
            die("无法获取 PlanRun 详情")
        report(pr)
        status = pr.get("status")
        if status not in PASSING_STATUSES:
            die(f"PlanRun 终态={status}，期望 SUCCESS 或 PARTIAL_SUCCESS")
    finally:
        client.close()


if __name__ == "__main__":
    main()
