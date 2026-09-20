#!/usr/bin/env python3
"""#703 ①：500 job / 30 host 规模 abort 的**现场压测腿**（seed → 并发读 + abort → 读序列）。

与 CI 里的 `backend/tests/services/test_plan_run_abort_scale.py` 分工：

| | CI 回归（pytest） | 本工具（dev 栈） |
|---|---|---|
| 判据 | 规模**不变性**（语句数、扇出合并、汇总 emit、样本如实） | 现场**读数**（借连接 p99、超时数、扇出桶、并发读成功率/延迟） |
| 为什么不能互换 | 单请求、进程内，量不出 p99 与真实并发 | 每次都要造数、耗时机器相关，不能进门禁 |

三条判据（`run` 全打印，任一越界即非零退出）：

1. `stability_db_pool_checkout_failures_total{engine,kind="timeout"}` 增量 = 0
   —— 就是 #703 现场那一下「拿不到连接」；
2. 借连接 p99 ≤ 1s（口径见指标 HELP：排队 + 建连 + pre-ping，不是纯排队时间）；
3. 并发读全部 200（过载时被踢回登录的那条链，起点就是读请求失败）；
   外加 `stability_plan_run_abort_fanout_jobs{scope="run"}` 本次样本数 = 造数总量
  （scope="host" 出现同量级样本 = #1880 类作用域错位复发）。

用法（仓根；DSN 只从环境读，不打印）::

    export DATABASE_URL='postgresql+psycopg://<user>:<pass>@127.0.0.1:15432/stp_dev'   # dev 栈
    python tools/dev/abort_scale_probe.py seed                     # 打印 plan_run_id
    python tools/dev/abort_scale_probe.py run --plan-run-id <id> --readers 8
    python tools/dev/abort_scale_probe.py cleanup --plan-run-id <id>

只允许打回环控制面（`--allow-remote` 才放行远端）；admin 凭据从
`STP_ADMIN_USER`/`STP_ADMIN_PASSWORD` 读（dev 栈默认值见 `docker-compose.yml`）。

**前置两条**（2026-09-17 首次跑通时踩到的）：

1. **控制面必须跑当前 main**——compose 的 dev 栈挂的是独立检出（`docker inspect` 看
   `/app` 的来源），它会长时间停在旧 revision：先确认目标实例的
   `backend/core/metrics.py` 有 `record_plan_run_abort_fanout`，否则这条腿量到的是
   没有埋点的旧世界（本工具会因扇出样本恒 0 而 FAIL，正是为了不静默）；
2. **实例要放宽 UI 限流桶**：默认 `STP_UI_RATE_LIMIT_REQUESTS=300/60s`，8 并发读几十秒
   就会 429，先于池触顶——本工具会把 429 如实报成「并发读非 200」，不会当成池问题。
   压测用的是专用实例（独立库 + 独立 redis db）时把它调大即可；**不要**为此改共享 dev 栈。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:18000"
HOSTS = 30
JOBS_PER_HOST = 17
RUNNING_PER_HOST = 6
PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


def _require_local(base_url: str, allow_remote: bool) -> None:
    host = base_url.split("//", 1)[-1].split("/", 1)[0].split(":")[0]
    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        raise SystemExit(
            f"拒绝打非回环控制面 {host}——压测腿只在 dev 栈跑；确需远端加 --allow-remote"
        )


#: dev 栈（`docker-compose.yml`）的库名与 PG 映射端口——seed/cleanup 只认这两个特征之一。
DEV_DB_NAME = "stp_dev"
DEV_DB_PORT = 15432


def _require_dev_db_target() -> None:
    """seed/cleanup 是**破坏性腿**（写 30 host / 510 job；cleanup 直接 DELETE 同规模）：
    import `backend.core.database`（import 期即建引擎）**之前**必须先确认目标是 dev 库。

    #2844：`backend.core.database` 的 DSN 经 `env_source.resolve_database_url()` 解析——
    ambient 没有 `DATABASE_URL` 时**静默回退仓库根 `.env.backend`（生产 env 源）**。
    忘一条 `export` 就把 seed/cleanup 打在真生产库上，而 run 腿有 `_require_local`
    拒绝非回环控制面、这三条腿此前**零守卫**（守卫不对称）。故这里 fail-closed：

    - 必须**显式**导出 `DATABASE_URL`（不接受任何文件兜底）；
    - 目标须是 dev 形态：库名 `stp_dev` 或 PG 端口 15432（compose 映射）。

    对照：`backend/core/db_url_guard.py`（#1300/#2632 系）守的是 `TEST_DATABASE_URL`；
    本探针的目标是 dev 栈（库名不含 test），故用 dev 特征而非 test 命名约定。
    """
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise SystemExit(
            "拒绝：seed/cleanup 是破坏性操作，必须**显式**导出 DATABASE_URL——"
            "未导出时 backend.core.database 会静默回退仓库根 .env.backend（生产 env 源，见 #2844）"
        )
    # SQLAlchemy 方言后缀（postgresql+psycopg://）urlsplit 认不出，剥掉再解析
    parts = urlsplit(re.sub(r"^[a-z0-9+]+://", "postgresql://", dsn, count=1, flags=re.I))
    dbname = parts.path.lstrip("/").split("?")[0]
    port = parts.port
    if dbname == DEV_DB_NAME or port == DEV_DB_PORT:
        return
    raise SystemExit(
        f"拒绝：目标库 {dbname or '?'}@{parts.hostname or '?'}:{port or '?'} 不像 dev 栈"
        f"（只接受库名 {DEV_DB_NAME} 或端口 {DEV_DB_PORT}）——生产/未知库上一律不动手"
    )


# ── seed / cleanup（直连 dev 库）──────────────────────────────────────────
def _session():
    from backend.core.database import SessionLocal

    return SessionLocal()


def cmd_seed(args: argparse.Namespace) -> int:
    _require_dev_db_target()
    from uuid import uuid4

    from sqlalchemy import insert

    from backend.models.enums import HostStatus, JobStatus, PlanRunStatus
    from backend.models.host import Device, Host
    from backend.models.job import JobInstance
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun, PlanRunHost

    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    db = _session()
    try:
        host_ids = [f"sc-{suffix}-{i:02d}" for i in range(args.hosts)]
        db.execute(
            insert(Host),
            [
                {
                    "id": hid,
                    "hostname": f"h-{hid}",
                    "status": HostStatus.ONLINE.value,
                    "created_at": now,
                }
                for hid in host_ids
            ],
        )
        plan_id = db.execute(
            insert(Plan)
            .values(
                name=f"scale-{suffix}",
                description="abort scale probe",
                created_by="abort_scale_probe",
            )
            .returning(Plan.id)
        ).scalar_one()
        run_id = db.execute(
            insert(PlanRun)
            .values(
                plan_id=plan_id,
                status=PlanRunStatus.RUNNING.value,
                plan_snapshot={"name": f"scale-{suffix}", "plan_id": plan_id},
                run_type="MANUAL",
                triggered_by="abort_scale_probe",
                started_at=now,
                total_job_count=args.hosts * args.jobs_per_host,
            )
            .returning(PlanRun.id)
        ).scalar_one()
        device_rows, layout = [], []
        for host_index, hid in enumerate(host_ids):
            for job_index in range(args.jobs_per_host):
                status = (
                    JobStatus.RUNNING
                    if job_index < args.running_per_host
                    else JobStatus.PENDING
                )
                device_rows.append(
                    {
                        "serial": f"SC{suffix.upper()}{host_index:02d}{job_index:02d}",
                        "host_id": hid,
                        "status": "ONLINE",
                        "tags": [],
                        "created_at": now,
                        "adb_connected": True,
                        "adb_state": "device",
                    }
                )
                layout.append((hid, status))
        device_ids = list(
            db.execute(insert(Device).returning(Device.id), device_rows).scalars()
        )
        db.execute(
            insert(JobInstance),
            [
                {
                    "plan_run_id": run_id,
                    "plan_id": plan_id,
                    "device_id": device_id,
                    "host_id": hid,
                    "status": status.value,
                    "pipeline_def": PIPELINE_DEF,
                    "created_at": now,
                    "updated_at": now,
                    "started_at": now if status is JobStatus.RUNNING else None,
                }
                for device_id, (hid, status) in zip(device_ids, layout, strict=True)
            ],
        )
        db.execute(
            insert(PlanRunHost),
            [
                {
                    "plan_run_id": run_id,
                    "host_id": hid,
                    "device_count": args.jobs_per_host,
                    "status": "ADMITTED",
                    "coordinator_epoch": 1,
                    "admitted_at": now,
                }
                for hid in host_ids
            ],
        )
        db.commit()
    finally:
        db.close()
    print(
        json.dumps(
            {
                "plan_run_id": run_id,
                "plan_id": plan_id,
                "hosts": len(host_ids),
                "jobs": len(host_ids) * args.jobs_per_host,
                "running": len(host_ids) * args.running_per_host,
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    _require_dev_db_target()
    from sqlalchemy import delete, select

    from backend.models.device_lease import DeviceLease
    from backend.models.host import Device, Host
    from backend.models.job import JobArtifact, JobInstance, StepTrace
    from backend.models.plan import Plan
    from backend.models.plan_run import PlanRun, PlanRunHost

    db = _session()
    try:
        run = db.execute(
            select(PlanRun).where(PlanRun.id == args.plan_run_id)
        ).scalar_one_or_none()
        if run is None:
            print(f"plan_run {args.plan_run_id} 不存在（可能已清）")
            return 0
        plan_id = run.plan_id
        job_ids = list(
            db.execute(
                select(JobInstance.id).where(JobInstance.plan_run_id == args.plan_run_id)
            ).scalars()
        )
        device_ids = list(
            db.execute(
                select(JobInstance.device_id).where(JobInstance.plan_run_id == args.plan_run_id)
            ).scalars()
        )
        host_ids = list(
            db.execute(
                select(PlanRunHost.host_id).where(PlanRunHost.plan_run_id == args.plan_run_id)
            ).scalars()
        )
        for model, column in (
            (DeviceLease, DeviceLease.job_id),
            (StepTrace, StepTrace.job_id),
            (JobArtifact, JobArtifact.job_id),
        ):
            if job_ids:
                db.execute(delete(model).where(column.in_(job_ids)))
        db.execute(delete(JobInstance).where(JobInstance.plan_run_id == args.plan_run_id))
        db.execute(delete(PlanRunHost).where(PlanRunHost.plan_run_id == args.plan_run_id))
        db.execute(delete(PlanRun).where(PlanRun.id == args.plan_run_id))
        db.execute(delete(Plan).where(Plan.id == plan_id))
        if device_ids:
            db.execute(delete(Device).where(Device.id.in_(device_ids)))
        if host_ids:
            db.execute(delete(Host).where(Host.id.in_(host_ids)))
        db.commit()
    finally:
        db.close()
    print(f"已清理 plan_run {args.plan_run_id}（{len(job_ids)} job / {len(host_ids)} host）")
    return 0


# ── run：并发读 + abort + 读序列 ───────────────────────────────────────────
def _fetch_metrics(base_url: str, session) -> dict[str, float]:
    resp = session.get(f"{base_url}/metrics", timeout=30)
    resp.raise_for_status()
    out: dict[str, float] = {}
    for line in resp.text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        try:
            out[name] = float(value)
        except ValueError:
            continue
    return out


def _series(metrics: dict[str, float], pattern: str) -> float:
    """匹配 pattern 的样本求和（用 search，容忍 label 顺序与额外 label）。"""
    rx = re.compile(pattern)
    return sum(value for name, value in metrics.items() if rx.search(name))


def _hist_quantile(
    metrics: dict[str, float], base: str, q: float, label_filter: str = ""
) -> float | None:
    buckets: list[tuple[float, float]] = []
    for name, value in metrics.items():
        match = re.fullmatch(re.escape(base) + r"_bucket\{(.*)\}", name)
        if not match or (label_filter and label_filter not in match.group(1)):
            continue
        le = re.search(r'le="([^"]+)"', match.group(1))
        if not le:
            continue
        edge = float("inf") if le.group(1) == "+Inf" else float(le.group(1))
        buckets.append((edge, value))
    if not buckets:
        return None
    buckets.sort()
    total = buckets[-1][1]
    if total <= 0:
        return None
    for edge, cumulative in buckets:
        if cumulative >= total * q:
            return edge
    return buckets[-1][0]


def cmd_run(args: argparse.Namespace) -> int:
    import requests

    _require_local(args.base_url, args.allow_remote)
    session = requests.Session()
    token = session.post(
        f"{args.base_url}/api/v1/auth/token",
        data={
            "username": os.getenv("STP_ADMIN_USER", "admin"),
            "password": os.getenv("STP_ADMIN_PASSWORD", "admin123"),
        },
        headers={"Origin": args.origin},
        timeout=15,
    ).json()["access_token"]
    session.headers["Authorization"] = f"Bearer {token}"

    stop = threading.Event()
    measuring = threading.Event()
    read_stats: list[tuple[int, float]] = []
    lock = threading.Lock()

    def reader(worker: int) -> None:
        path = "plan-runs?limit=20" if worker % 2 else "hosts?limit=20"
        while not stop.is_set():
            started = time.perf_counter()
            try:
                code = session.get(f"{args.base_url}/api/v1/{path}", timeout=30).status_code
            except Exception:
                code = -1
            if measuring.is_set():
                with lock:
                    read_stats.append((code, (time.perf_counter() - started) * 1000))

    threads = [
        threading.Thread(target=reader, args=(i,), daemon=True) for i in range(args.readers)
    ]
    for thread in threads:
        thread.start()
    time.sleep(args.warmup)

    before = _fetch_metrics(args.base_url, session)
    measuring.set()
    started = time.perf_counter()
    abort_status = session.post(
        f"{args.base_url}/api/v1/plan-runs/{args.plan_run_id}/abort", timeout=120
    ).status_code
    abort_seconds = time.perf_counter() - started
    measuring.clear()
    stop.set()
    for thread in threads:
        thread.join(timeout=30)
    time.sleep(args.settle)
    after = _fetch_metrics(args.base_url, session)

    def delta(pattern: str) -> float:
        return round(_series(after, pattern) - _series(before, pattern), 6)

    codes = [code for code, _ms in read_stats]
    latencies = sorted(ms for _code, ms in read_stats) or [0.0]
    pool_p99 = _hist_quantile(
        after, "stability_db_pool_checkout_seconds", 0.99, 'engine="sync"'
    )
    # 扇出是**直方图**：`_count` 是 abort 次数、`_sum` 才是这次牵动的 job 数。
    # 桶判读回答「样本落在哪个桶」——`le="500.0"` 那档为 0、`le="1000.0"` 为 1
    # 即「510 个 job 的样本落在 (500, 1000]」。
    fanout_samples = delta(r"^stability_plan_run_abort_fanout_jobs_count\{[^}]*scope=\"run\"")
    fanout_jobs = delta(r"^stability_plan_run_abort_fanout_jobs_sum\{[^}]*scope=\"run\"")
    fanout_host = delta(r"^stability_plan_run_abort_fanout_jobs_count\{[^}]*scope=\"host\"")
    fanout_bucket = None
    for name, cumulative in after.items():
        match = re.fullmatch(r"stability_plan_run_abort_fanout_jobs_bucket\{(.*)\}", name)
        if not match or 'scope="run"' not in match.group(1):
            continue
        le = re.search(r'le="([^"]+)"', match.group(1))
        if not le or "Inf" in le.group(1):
            continue
        if cumulative >= fanout_samples:
            edge = float(le.group(1))
            fanout_bucket = edge if fanout_bucket is None else min(fanout_bucket, edge)

    summary = {
        "abort": {"http_status": abort_status, "seconds": round(abort_seconds, 3)},
        "reads": {
            "count": len(read_stats),
            "ok_200": sum(1 for code in codes if code == 200),
            "non_200": sorted({code for code in codes if code != 200}),
            "p50_ms": round(statistics.median(latencies), 1),
            "p99_ms": round(latencies[max(0, int(len(latencies) * 0.99) - 1)], 1),
        },
        "db_pool": {
            "checkout_timeout_delta": delta(
                r'^stability_db_pool_checkout_failures_total\{[^}]*kind="timeout"'
            ),
            "checkout_error_delta": delta(
                r'^stability_db_pool_checkout_failures_total\{[^}]*kind="error"'
            ),
            "checkout_p99_seconds": pool_p99,
        },
        "fanout": {
            "run_samples_delta": fanout_samples,
            "run_jobs_delta": fanout_jobs,
            "host_samples_delta": fanout_host,
            "run_bucket_le": fanout_bucket,
            "expected_jobs": args.jobs_expected,
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    failures: list[str] = []
    if abort_status != 200:
        failures.append(f"abort HTTP {abort_status}")
    if summary["db_pool"]["checkout_timeout_delta"] > 0:
        failures.append(f"池 checkout 超时 {summary['db_pool']['checkout_timeout_delta']} 次")
    if pool_p99 is not None and pool_p99 > args.p99_budget:
        failures.append(f"借连接 p99 {pool_p99}s > 预算 {args.p99_budget}s")
    if any(code != 200 for code in codes):
        failures.append(f"并发读非 200：{sorted({c for c in codes if c != 200})}")
    if fanout_samples != 1:
        failures.append(f"扇出样本条数 {fanout_samples} != 1")
    if fanout_jobs != args.jobs_expected:
        failures.append(f"扇出 job 数 {fanout_jobs} != 造数 {args.jobs_expected}")
    if fanout_host > 0:
        failures.append(f"出现 scope=host 样本 {fanout_host}（作用域错位疑似复发）")
    if failures:
        print("\n[FAIL] " + "；".join(failures), file=sys.stderr)
        return 1
    print("\n[OK] 规模压测腿：无池超时、并发读无失败、扇出样本如实落在 run 作用域")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="造 30 host × 17 job 的 RUNNING run（6 RUNNING/11 PENDING）")
    seed.add_argument("--hosts", type=int, default=HOSTS)
    seed.add_argument("--jobs-per-host", type=int, default=JOBS_PER_HOST)
    seed.add_argument("--running-per-host", type=int, default=RUNNING_PER_HOST)
    seed.set_defaults(func=cmd_seed)

    clean = sub.add_parser("cleanup", help="清理本次造的 run")
    clean.add_argument("--plan-run-id", type=int, required=True)
    clean.set_defaults(func=cmd_cleanup)

    run = sub.add_parser("run", help="并发读 + abort + 读 /metrics 判读")
    run.add_argument("--plan-run-id", type=int, required=True)
    run.add_argument("--base-url", default=DEFAULT_BASE_URL)
    run.add_argument("--origin", default="http://localhost:15173")
    run.add_argument("--readers", type=int, default=8)
    run.add_argument("--warmup", type=float, default=1.0)
    run.add_argument("--settle", type=float, default=1.0)
    run.add_argument("--p99-budget", type=float, default=1.0)
    run.add_argument("--jobs-expected", type=int, default=HOSTS * JOBS_PER_HOST)
    run.add_argument("--allow-remote", action="store_true")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
