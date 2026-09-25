"""#3243：R523 型回流压测——490 RUNNING 同时 abort + 并发终态回传（8 条验收线）。

现场（2026-09-23，plan_run 523）的形态：37 台 host / 494 个 job（490 RUNNING + 4 FAILED），
一次手动 abort 让 490 个在跑作业**同时**回传终态；控制面当时被 1644 次 `/complete`
（3.35× 放大）打穿连接槽。本用例把那个形态搬进 PG（testcontainers / `TEST_DATABASE_URL`），
并把 owner 的 8 条验收线变成断言：

1. `slots_exhausted` / `timeout` 取连接失败 **= 0**（ADR-0047 D1/D2 的硬不变量）；
2. HTTP **500 = 0**；背压只允许是结构化 503（舱壁/过载）；
3. `/complete` 与 `/heartbeat`（控制面可响应性代理）**p99 < 1s**；
4. 池不越预算：`checked_out` 峰值 ≤ 每引擎上限（`pool_capacity()`，单一读数口）；
5. 490 条终态事实**最终全部 ACK**（200 或 409-幂等），无 RUNNING 残留；
6. 计数一致：`plan_run` / `plan_run_host` 计数器 = 实重数；
7. PlanRun 在 **120s** 内收敛终态；
8. 恢复**不依赖重启**（全程同一个 app 实例，无进程重启、无人工干预）。

**每条线先证明「量到了」再判「多快/多少」**（#3247 / 2026-09-25 审计 A01）：零样本、
全 503、单端点失效曾都能让第 3 条的 p99 通过；前序用例 dispose 过 async 引擎后，
第 1、4 条读的是已拔掉的池观测（CI 全量里 async 池峰恒 0.0）。故探针按端点要求样本数
与全成功，池采样与取连接观测都必须有非零读数，否则判红而不是判绿。

第 3 条在本用例里的口径是 heartbeat / `/health` 探针。被接纳的 `/complete(200)` p99 只进
校准摘要、**不在此断言**：它量的是父 Run 行锁串行段（#3244 / ADR-0052 的热点），且随
runner 负载漂移（同一提交本地 1.1s、共享 CI runner 2.9s）——延迟 SLO 在固定资源的
容量环境（#105 阶梯）与 ADR-0052 §5 真机门槛里判定，共享 CI 只守不变量（owner 2026-09-25 裁决）。

同时输出一份**校准摘要**（`CALIBRATION_3243` 一行 JSON）：池峰、p50/p99、503 数、
重放轮次、分端点探针——正好是 ADR-0047 §4 要回填的分布。

与既有 `test_plan_run_abort_scale.py` 的分工：那支钉**abort 请求形状**（语句数不随 job 数
线性增长）且断言 RUNNING 保持 RUNNING 等 ack；本支钉**回流**（ack 之后的那半场）。

覆盖不到的（如实列出，别读成全绿）：
- 真实 Agent 进程与 SocketIO：本用例用 in-process ASGI +「首发单发 / 每机并发 2 / 失败交
  outbox 重放」的等价驱动（#3251 的策略），不是 48 台真机；
- `HostHeartbeatTimeout` 告警与 UI 会话端点：分别以 heartbeat 延迟与 `/health` 延迟代理，
  真机口径见 #3243 的部署窗复跑。
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from threading import Thread
from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest
from prometheus_client import REGISTRY
from sqlalchemy import insert, select, text

from backend.core.database import SessionLocal, pool_capacity
from backend.main import fastapi_app
from backend.models.device_lease import DeviceLease
from backend.models.enums import HostStatus, JobStatus, LeaseStatus, LeaseType, PlanRunStatus
from backend.models.host import Device, Host
from backend.models.job import JobArtifact, JobInstance, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun, PlanRunHost
from backend.services.plan_run_abort import abort_plan_run
from backend.core import thread_pool

# ── R523 的形状 ──────────────────────────────────────────────────────────────
HOSTS = 37
BASE_JOBS_PER_HOST = 13  # 37×13 = 481
EXTRA_ON_FIRST_HOST = 13  # +13 → 494（单机 max 26，覆盖 R523 的 max 23）
TOTAL_JOBS = HOSTS * BASE_JOBS_PER_HOST + EXTRA_ON_FIRST_HOST  # 494
FAILED_TOTAL = 4
RUNNING_TOTAL = TOTAL_JOBS - FAILED_TOTAL  # 490

#: 与线上默认值一致（#3241 舱壁 / #3251 Agent 削峰）；压测不调参，验的就是默认口径。
PER_HOST_UPLOAD_CONCURRENCY = 2
CONVERGENCE_BUDGET_SECONDS = 120.0
P99_BUDGET_SECONDS = 1.0
#: 「outbox 重放」：真机 Agent 每个 drain 周期（15s + jitter）重放到 ACK 为止，不设轮数上限。
#: 旧版固定 3 轮，收敛与否取决于 runner 快慢（CI 第 3 轮后仍剩 92 条未 ACK，#3247）。
#: 这里重放到收敛或耗尽 `CONVERGENCE_BUDGET_SECONDS`（验收线 7 的同一预算）为止。
REPLAY_INTERVAL_SECONDS = 0.5  # 真机 15s 的 drain 周期压缩（契约不变，只是不等真时钟）
REPLAY_ROUND_CAP = 400  # 迭代上界（testing.md §7）：墙钟预算之外的第二道闸，正常到不了
#: 探针判据：每个端点至少这么多次尝试，且**全部** 200——样本不足本身就是失败。
PROBE_ENDPOINTS = (("/api/v1/heartbeat", "post"), ("/health", "get"))
MIN_PROBE_SAMPLES_PER_ENDPOINT = 5

_AGENT_SECRET = "3243-scale-secret"
_PIPELINE_DEF = {"lifecycle": {"init": [], "teardown": []}}


@pytest.fixture
def agent_secret(monkeypatch):
    monkeypatch.setenv("AGENT_SECRET", _AGENT_SECRET)
    return _AGENT_SECRET


@pytest.fixture
def fast_grace(monkeypatch):
    """把「同 host 冷却」等长等待压到 0——它们与判据无关，但会让用例变成分钟级。"""
    monkeypatch.setenv("STP_ABORT_HOST_COOLDOWN_SECONDS", "0")
    yield


def _seed_r523_shape() -> dict:
    """37 host / 494 job（490 RUNNING + 4 FAILED）+ 每 job 一条 ACTIVE JOB 租约。

    租约是**必须**的：`/complete` 对 RUNNING 作业校验 ACTIVE 租约 + fencing token，
    没有它拿不到 409 之外的任何结果（R523 现场 490 个作业都持有效租约）。
    """
    suffix = uuid4().hex[:8]
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        host_ids = [f"bf-{suffix}-{i:02d}" for i in range(HOSTS)]
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
            .values(name=f"backflow-{suffix}", description="R523 backflow scale", created_by="pytest")
            .returning(Plan.id)
        ).scalar_one()
        run_id = db.execute(
            insert(PlanRun)
            .values(
                plan_id=plan_id,
                status=PlanRunStatus.RUNNING.value,
                plan_snapshot={"name": f"backflow-{suffix}", "plan_id": plan_id},
                run_type="MANUAL",
                triggered_by="pytest",
                started_at=now,
                total_job_count=TOTAL_JOBS,
                # 4 个 FAILED 是 run 早期就已终态化的作业：计数器在它们终态时就已 +1，
                # 种子必须带上（否则 terminal_job_count 永远差 4，run 不收敛）。
                terminal_job_count=FAILED_TOTAL,
                failed_job_count=FAILED_TOTAL,
            )
            .returning(PlanRun.id)
        ).scalar_one()

        host_of_job: list[str] = []
        status_of_job: list[JobStatus] = []
        for index, hid in enumerate(host_ids):
            count = BASE_JOBS_PER_HOST + (EXTRA_ON_FIRST_HOST if index == 0 else 0)
            for _ in range(count):
                host_of_job.append(hid)
                status_of_job.append(JobStatus.RUNNING)

        # 尾部 4 个改成 FAILED（R523 的 490 + 4 形状）
        for offset in range(FAILED_TOTAL):
            status_of_job[len(status_of_job) - 1 - offset] = JobStatus.FAILED

        device_rows = [
            {
                "serial": f"BF{suffix.upper()}{i:04d}",
                "host_id": hid,
                "status": "ONLINE",
                "tags": [],
                "created_at": now,
                "adb_connected": True,
                "adb_state": "device",
            }
            for i, hid in enumerate(host_of_job)
        ]
        device_ids = list(db.execute(insert(Device).returning(Device.id), device_rows).scalars())
        job_rows = [
            {
                "plan_run_id": run_id,
                "plan_id": plan_id,
                "device_id": device_id,
                "host_id": hid,
                "status": status.value,
                "pipeline_def": _PIPELINE_DEF,
                "created_at": now,
                "updated_at": now,
                "started_at": now if status is JobStatus.RUNNING else None,
                "ended_at": now if status is JobStatus.FAILED else None,
            }
            for device_id, hid, status in zip(device_ids, host_of_job, status_of_job, strict=True)
        ]
        job_ids = list(db.execute(insert(JobInstance).returning(JobInstance.id), job_rows).scalars())
        tokens = {job_id: f"tok-{job_id}-{suffix}" for job_id in job_ids}
        db.execute(
            insert(DeviceLease),
            [
                {
                    "device_id": device_id,
                    "job_id": job_id,
                    "host_id": hid,
                    "lease_type": LeaseType.JOB.value,
                    "status": LeaseStatus.ACTIVE.value,
                    "fencing_token": tokens[job_id],
                    "lease_generation": 1,
                    "agent_instance_id": f"agent-{suffix}",
                    "acquired_at": now,
                    "renewed_at": now,
                    "expires_at": now + timedelta(hours=2),
                }
                for device_id, job_id, hid, status in zip(
                    device_ids, job_ids, host_of_job, status_of_job, strict=True
                )
                if status is JobStatus.RUNNING
            ],
        )
        # 4 个 FAILED 落在**末位 host**（上面的播种顺序）：该 host 的 PlanRunHost
        # 行必须预置 terminal/failed 计数——与 run 级同因（否则差 4，计数对不上实重数）。
        failed_host = host_ids[-1]
        db.execute(
            insert(PlanRunHost),
            [
                {
                    "plan_run_id": run_id,
                    "host_id": hid,
                    "device_count": sum(1 for h in host_of_job if h == hid),
                    "terminal_job_count": FAILED_TOTAL if hid == failed_host else 0,
                    "failed_job_count": FAILED_TOTAL if hid == failed_host else 0,
                    "status": "ADMITTED",
                    "coordinator_epoch": 1,
                    "admitted_at": now,
                }
                for hid in host_ids
            ],
        )
        db.commit()
        running = [jid for jid, status in zip(job_ids, status_of_job, strict=True)
                   if status is JobStatus.RUNNING]
        return {
            "host_ids": host_ids,
            "plan_id": plan_id,
            "plan_run_id": run_id,
            "device_ids": device_ids,
            "job_ids": job_ids,
            "running_job_ids": running,
            "tokens": tokens,
            "host_of_job": dict(zip(job_ids, host_of_job, strict=True)),
            "serials_of_host": {
                hid: [row["serial"] for row in device_rows if row["host_id"] == hid] for hid in host_ids
            },
        }
    finally:
        db.close()


def _cleanup(seed: dict) -> None:
    db = SessionLocal()
    try:
        db.execute(text("SET statement_timeout = '30s'"))
        for model, column in (
            (DeviceLease, DeviceLease.job_id),
            (StepTrace, StepTrace.job_id),
            (JobArtifact, JobArtifact.job_id),
        ):
            db.query(model).filter(column.in_(seed["job_ids"])).delete()
        db.query(JobInstance).filter(JobInstance.id.in_(seed["job_ids"])).delete()
        db.query(PlanRunHost).filter(PlanRunHost.plan_run_id == seed["plan_run_id"]).delete()
        db.query(PlanRun).filter(PlanRun.id == seed["plan_run_id"]).delete()
        db.query(Plan).filter(Plan.id == seed["plan_id"]).delete()
        db.query(Device).filter(Device.id.in_(seed["device_ids"])).delete()
        db.query(Host).filter(Host.id.in_(seed["host_ids"])).delete()
        db.commit()
    finally:
        db.close()


def _abort(seed: dict) -> None:
    """真实 abort（只替掉与判据无关的外呼：socketio / 通知 / 锁时长观测）。"""
    with (
        patch("backend.services.plan_run_abort.notify_plan_run_terminal", lambda *a, **k: None),
        patch("backend.services.plan_run_abort.record_plan_run_abort_lock_seconds", lambda *a, **k: None),
        patch("backend.services.plan_run_abort.record_plan_run_abort_fanout", lambda *a, **k: None),
        patch("backend.services.plan_run_abort.schedule_agent_control_fanout", lambda items: None),
        patch("backend.services.plan_run_abort.schedule_emit", lambda *a, **k: None),
        # 终态收敛会外呼 chain / dedup（与判据无关，且会给本用例留下他计划的 run）
        patch("backend.services.plan_chain_trigger.trigger_next_plan_sync", lambda *a, **k: None),
        patch("backend.services.dedup_scan.enqueue_dedup_terminal_sync", lambda *a, **k: None),
    ):
        db = SessionLocal()
        try:
            abort_plan_run(seed["plan_run_id"], db=db, reason="pytest-3243")
            db.commit()
        finally:
            db.close()


class _RecordingQueue:
    """SAQ 队列替身：只记录 post_completion 入队（本用例不启 lifepan / 不连 Redis）。

    现场那 491 次 `saq_post_completion_start` 是 R523 的噪声来源之一；这里断言
    「每个终态恰好扇出一次」而不是真的跑后处理（真跑需要 Redis + worker，属部署窗）。
    """

    def __init__(self) -> None:
        self.enqueued: dict[int, dict] = {}
        self.duplicates = 0
        #: 非 post_completion 的入队（同窗口其它 get_queue() 调用方）——单独收，
        #: 别混进扇出计数（首版实测：混进来一条无 job_id 的条目，491≠490）。
        self.other_functions: list[str] = []

    async def enqueue(self, job) -> None:
        """只记 `post_completion_task`，并按 job_id 去重（复刻 SAQ `key=pc:{job_id}`）。

        重放（同 job 二次 /complete）在生产会再调一次 enqueue，但 SAQ 按 key 折叠；
        本替身若按调用计数，会把「幂等重放」误判成扇出漂移。
        """
        if getattr(job, "function", None) != "post_completion_task":
            self.other_functions.append(str(getattr(job, "function", None)))
            return
        kwargs = getattr(job, "kwargs", None) or {}
        job_id = kwargs.get("job_id")
        if job_id in self.enqueued:
            self.duplicates += 1
            return
        self.enqueued[job_id] = kwargs


def _metric(name: str, labels: dict | None = None) -> float:
    return REGISTRY.get_sample_value(name, labels or {}) or 0.0


class _PoolSampler:
    """池峰值采样（与生产同一批指标：#3241 的 stability_db_pool_checked_out）。"""

    def __init__(self, interval: float = 0.025):
        self._interval = interval
        self._stop = False
        self.peaks: dict[str, float] = {"async": 0.0, "sync": 0.0}
        self.samples = 0

    def start(self) -> Thread:
        def _run():
            while not self._stop:
                for engine in ("async", "sync"):
                    self.peaks[engine] = max(
                        self.peaks[engine],
                        _metric("stability_db_pool_checked_out", {"engine": engine}),
                    )
                self.samples += 1
                time.sleep(self._interval)

        thread = Thread(target=_run, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop = True


def _p99(sorted_values: list[float]) -> float:
    """与首版校准同一取位口径（`#3243` Note 的历史数字可比）；调用方保证非空。"""
    return sorted_values[int(len(sorted_values) * 0.99) - 1]


def _probe_stats(probe: list[dict]) -> dict[str, dict]:
    """按端点统计探针：尝试数 / 200 数 / 非 200 分布 / 200 样本的 p99（秒）。"""
    stats: dict[str, dict] = {}
    for path, _method in PROBE_ENDPOINTS:
        rows = [p for p in probe if p["path"] == path]
        ok = sorted(p["elapsed"] for p in rows if p["status"] == 200)
        non_200: dict[str, int] = {}
        for p in rows:
            if p["status"] != 200:
                non_200[str(p["status"])] = non_200.get(str(p["status"]), 0) + 1
        stats[path] = {
            "attempted": len(rows),
            "ok": len(ok),
            "non_200": non_200,
            "p99_ok": _p99(ok) if ok else None,
        }
    return stats


def _probe_violations(stats: dict[str, dict]) -> list[str]:
    """验收线 3 的判据：**先**证明每个端点都量到了且全成功，**再**比 p99。

    #3247（A01）：旧判据两端点混算、只取 200 的延迟、零样本回退 ``[0.0]``——零样本、
    全 503、heartbeat 全挂而 `/health` 正常，三种都能让「p99 < 1s」通过。
    """
    problems: list[str] = []
    for path, _method in PROBE_ENDPOINTS:
        s = stats.get(path) or {"attempted": 0, "ok": 0, "non_200": {}, "p99_ok": None}
        if s["attempted"] < MIN_PROBE_SAMPLES_PER_ENDPOINT:
            problems.append(f"{path} 探针仅 {s['attempted']} 次（< {MIN_PROBE_SAMPLES_PER_ENDPOINT}）：没量到不等于快")
        if s["ok"] != s["attempted"]:
            problems.append(f"{path} 非 200：{s['non_200']}（-1 = 网络层异常）")
        if s["p99_ok"] is not None and s["p99_ok"] >= P99_BUDGET_SECONDS:
            problems.append(f"{path} p99={s['p99_ok']:.3f}s ≥ {P99_BUDGET_SECONDS}s")
    return problems


async def _post_complete(client: httpx.AsyncClient, job_id: int, token: str) -> dict:
    started = time.perf_counter()
    response = await client.post(
        f"/api/v1/agent/jobs/{job_id}/complete",
        json={"update": {"status": "CANCELED"}, "fencing_token": token},
    )
    elapsed = time.perf_counter() - started
    return {
        "job_id": job_id,
        "status": response.status_code,
        "elapsed": elapsed,
        "body": response.text[:200],
    }


async def _drive_backflow(seed: dict, agent_secret: str, deadline: float) -> dict:
    """并发回传 490 个终态：首发单发 + 每机并发 2 + 失败交「outbox 重放」轮次。

    与 #3251 的 Agent 策略同形（本用例不引入真机，只复刻策略）：
    - 首发每个 job **只打一次**（失败不线程内重试）；
    - 每台 host 同刻最多 `PER_HOST_UPLOAD_CONCURRENCY` 个在飞；
    - 失败（503/429/5xx/网络）进入下一轮重放，轮间等待一小段（真机是 15s drain 周期），
      重放到 ACK 或 ``deadline``（`perf_counter` 刻度，= 验收线 7 的收敛预算）为止。
    """
    transport = httpx.ASGITransport(app=fastapi_app)
    results: list[dict] = []
    p99_probe: list[dict] = []
    host_semaphores = {hid: asyncio.Semaphore(PER_HOST_UPLOAD_CONCURRENCY) for hid in seed["host_ids"]}
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Agent-Secret": agent_secret},
        timeout=30.0,
    ) as client:

        async def _one(job_id: int) -> None:
            async with host_semaphores[seed["host_of_job"][job_id]]:
                record = await _post_complete(client, job_id, seed["tokens"][job_id])
            async with lock:
                results.append(record)

        # 真实 Agent 形状的心跳：必填 `status` + 本机设备清单。首版缺 `status`，每次都 422
        # 而旧判据只取 200 的延迟，于是 heartbeat 一次都没量到也照样「p99 通过」（#3247）；
        # 空设备清单又会让心跳把该 host 的设备标 OFFLINE（`_mark_missing_devices_offline`），
        # 改写被测场景——所以按种子状态如实上报。
        probe_host = seed["host_ids"][0]
        heartbeat_payload = {
            "host_id": probe_host,
            "status": "ONLINE",
            "agent_version": "pytest",
            "devices": [
                {"serial": serial, "adb_state": "device", "adb_connected": True}
                for serial in seed["serials_of_host"][probe_host]
            ],
        }

        async def _probe_loop() -> None:
            """背景可响应性探针（heartbeat / health）——验收线 3 的测量面。"""
            while True:
                for path, method in PROBE_ENDPOINTS:
                    started = time.perf_counter()
                    try:
                        if method == "post":
                            resp = await client.post(path, json=heartbeat_payload)
                        else:
                            resp = await client.get(path)
                        status = resp.status_code
                    except Exception:  # noqa: BLE001 — 网络层异常也要计数，不能吞
                        status = -1
                    async with lock:
                        p99_probe.append(
                            {"path": path, "status": status, "elapsed": time.perf_counter() - started}
                        )
                await asyncio.sleep(0.2)

        probe_task = asyncio.create_task(_probe_loop())

        round_log: list[dict] = []
        pending = list(seed["running_job_ids"])
        for round_index in range(REPLAY_ROUND_CAP):
            round_started = time.perf_counter()
            attempted_now = len(pending)
            await asyncio.gather(*(_one(job_id) for job_id in pending))
            accepted = {r["job_id"] for r in results if r["status"] in (200, 409)}
            pending = [job_id for job_id in seed["running_job_ids"] if job_id not in accepted]
            round_log.append(
                {
                    "round": round_index,
                    "attempted": attempted_now,
                    "remaining": len(pending),
                    "elapsed": round(time.perf_counter() - round_started, 3),
                }
            )
            if not pending or time.perf_counter() >= deadline:
                break
            await asyncio.sleep(REPLAY_INTERVAL_SECONDS)

        probe_task.cancel()
        try:
            await probe_task
        except asyncio.CancelledError:
            pass

    return {"results": results, "probe": p99_probe, "rounds": round_log, "pending": pending}


async def _dispose_async_engine() -> None:
    """释放本次 loop 上的池连接（否则收尾时 app 的 async engine 跨 loop 释放 → 噪声栈）。

    只 dispose 不重建：下一次用时会按需重连（对其它测试无影响）。
    """
    from backend.core import database

    await database.async_engine.dispose()


def _recompute_counters(plan_run_id: int) -> dict:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(JobInstance.status).where(JobInstance.plan_run_id == plan_run_id)
        ).scalars().all()
        by_status: dict[str, int] = {}
        for status in rows:
            by_status[status] = by_status.get(status, 0) + 1
        per_host = db.execute(
            text(
                "SELECT host_id, count(*) AS c FROM job_instance "
                "WHERE plan_run_id = :rid GROUP BY host_id"
            ),
            {"rid": plan_run_id},
        ).fetchall()
        return {
            "total": len(rows),
            "terminal": sum(1 for s in rows if s in ("COMPLETED", "FAILED", "ABORTED")),
            "aborted": by_status.get("ABORTED", 0),
            "failed": by_status.get("FAILED", 0),
            "running": by_status.get("RUNNING", 0),
            "by_status": by_status,
            "per_host": {row.host_id: row.c for row in per_host},
        }
    finally:
        db.close()


async def test_r523_backflow_meets_all_acceptance_lines(agent_secret, fast_grace):
    """主用例：一次跑完 8 条验收线，并输出校准摘要。

    单事件循环：控制面 app 的 async engine 池连接绑定创建它的 loop，跨 `asyncio.run`
    会留下「Event loop is closed」的假故障（首版实测）。摘要**先打印后断言**：
    失败时也要拿到分布，否则「只在全绿时才看得到数」会让校准拿不到证据。
    """
    slots_before = _metric(
        "stability_db_pool_checkout_failures_total", {"engine": "async", "kind": "slots_exhausted"}
    )
    timeout_before = _metric(
        "stability_db_pool_checkout_failures_total", {"engine": "async", "kind": "timeout"}
    )
    # 验收线 1 的「表在不在线」：取连接观测计数必须随本次流量增长，否则失败计数恒 0 也不可信
    checkouts_before = _metric("stability_db_pool_checkout_seconds_count", {"engine": "async"})
    cap = pool_capacity()

    seed = _seed_r523_shape()
    sampler = _PoolSampler()
    sampler.start()
    started = time.perf_counter()
    queue = _RecordingQueue()
    try:
        with patch("backend.tasks.saq_worker.get_queue", lambda: queue):
            _abort(seed)
            driver = await _drive_backflow(
                seed, agent_secret, deadline=started + CONVERGENCE_BUDGET_SECONDS
            )
        elapsed_total = time.perf_counter() - started
        # 聚合 / 通知等后台项排空（不是重启，等价于等一个 tick）
        await asyncio.to_thread(thread_pool.drain, 30)
    finally:
        sampler.stop()

    try:
        # ── 观测值（全部先算出来，再打印摘要，最后断言） ────────────────────
        slots_after = _metric(
            "stability_db_pool_checkout_failures_total",
            {"engine": "async", "kind": "slots_exhausted"},
        )
        timeout_after = _metric(
            "stability_db_pool_checkout_failures_total", {"engine": "async", "kind": "timeout"}
        )
        checkouts_observed = (
            _metric("stability_db_pool_checkout_seconds_count", {"engine": "async"}) - checkouts_before
        )
        statuses = [r["status"] for r in driver["results"]]
        five_hundreds = [r for r in driver["results"] if r["status"] == 500]
        unexpected = sorted({s for s in statuses if s not in (200, 409, 503)})

        complete_latencies = sorted(r["elapsed"] for r in driver["results"])
        ok_latencies = sorted(r["elapsed"] for r in driver["results"] if r["status"] == 200)
        probe_ok_latencies = sorted(p["elapsed"] for p in driver["probe"] if p["status"] == 200)
        p99_complete = _p99(complete_latencies)
        p99_ok = _p99(ok_latencies) if ok_latencies else None
        probe = _probe_stats(driver["probe"])

        counts = _recompute_counters(seed["plan_run_id"])
        db = SessionLocal()
        try:
            run = db.get(PlanRun, seed["plan_run_id"])
            assert run is not None
            run_status = run.status
            run_terminal = run.terminal_job_count
            run_aborted = run.aborted_job_count
            run_failed = run.failed_job_count
            host_counts = {
                row.host_id: row.terminal_job_count
                for row in db.execute(
                    select(PlanRunHost).where(PlanRunHost.plan_run_id == seed["plan_run_id"])
                ).scalars().all()
            }
        finally:
            db.close()

        summary = {
            "jobs": TOTAL_JOBS,
            "running": RUNNING_TOTAL,
            "requests": len(driver["results"]),
            "amplification": round(len(driver["results"]) / RUNNING_TOTAL, 3),
            "shed_503": statuses.count(503),
            "status_counts": {str(code): statuses.count(code) for code in sorted(set(statuses))},
            "p50_complete_ms": round(statistics.median(complete_latencies) * 1000, 1),
            "p50_complete_ok_ms": round(statistics.median(ok_latencies) * 1000, 1) if ok_latencies else 0.0,
            "p90_complete_ok_ms": round(ok_latencies[int(len(ok_latencies) * 0.9) - 1] * 1000, 1) if ok_latencies else 0.0,
            "p99_complete_ms": round(p99_complete * 1000, 1),
            # 只记录不断言（共享 runner 上随负载漂移）；判定在容量环境与 ADR-0052 §5 真机门槛
            "p99_complete_ok_ms": round(p99_ok * 1000, 1) if p99_ok is not None else None,
            # 两端点合并的 200 样本 p99：与首版校准同口径，便于和 #3243 Note 的历史数对照
            "p99_probe_ms": round(_p99(probe_ok_latencies) * 1000, 1) if probe_ok_latencies else None,
            "probe_samples": len(driver["probe"]),
            "probe_by_endpoint": {
                path: {
                    "attempted": s["attempted"],
                    "ok": s["ok"],
                    "non_200": s["non_200"],
                    "p99_ok_ms": round(s["p99_ok"] * 1000, 1) if s["p99_ok"] is not None else None,
                }
                for path, s in probe.items()
            },
            "pool_peak_async": sampler.peaks["async"],
            "pool_peak_sync": sampler.peaks["sync"],
            "pool_samples": sampler.samples,
            "pool_checkouts_observed_async": checkouts_observed,
            "pool_budget_per_engine": cap["per_engine"],
            "post_completion_enqueued": len(queue.enqueued),
            "post_completion_duplicates": queue.duplicates,
            "queue_other_functions": sorted(set(queue.other_functions)),
            "enqueue_ids_outside_running": sorted(
                str(k) for k in set(queue.enqueued) - set(seed["running_job_ids"])
            )[:5],
            "rounds": driver["rounds"],
            "converged_seconds": round(elapsed_total, 2),
            "run_status": run_status,
        }
        # 摘要先打印：失败时也要能读到分布（校准证据不依赖全绿）
        print("CALIBRATION_3243 " + json.dumps(summary, ensure_ascii=False))

        # ── 验收线 1：取连接失败 = 0（ADR-0047 D1/D2 的硬不变量） ───────────
        # 先证明观测在线：换池（dispose）后观测曾静默脱线，失败计数恒 0 也就不可信（#3247）
        assert checkouts_observed > 0, "async 取连接观测零增长：验收线 1 无从判定（观测未接到当前池）"
        assert slots_after - slots_before == 0, "出现 slots_exhausted：R523 的形态回来了"
        assert timeout_after - timeout_before == 0, "出现池排队超时：pool_timeout 口径失守"

        # ── 验收线 2：500 = 0，背压只能是结构化 503 ─────────────────────────
        assert not five_hundreds, f"出现 500（应只有结构化 503）：{five_hundreds[:3]}"
        assert not unexpected, f"出现非预期状态码：{unexpected}"

        # ── 验收线 3：可响应性 —— owner 口径（UI/heartbeat）p99 < 1s ─────────
        # 分端点：样本数够、全部 200、p99 达标，三者缺一即红。
        # 被接纳的 `/complete(200)` p99 见 summary.p99_complete_ok_ms，本用例不断言（模块 docstring）。
        violations = _probe_violations(probe)
        assert not violations, "；".join(violations)

        # ── 验收线 4：池不越预算（每引擎上限 = pool_size + max_overflow） ────
        assert sampler.peaks["async"] > 0, (
            f"async 池峰 0（{sampler.samples} 次采样）：/complete 走 async 池，读 0 说明采样没接到当前池"
        )
        assert sampler.peaks["async"] <= cap["per_engine"], (
            f"async 池峰 {sampler.peaks['async']} > 上限 {cap['per_engine']}"
        )
        assert sampler.peaks["sync"] <= cap["per_engine"], (
            f"sync 池峰 {sampler.peaks['sync']} > 上限 {cap['per_engine']}"
        )

        # ── 后处理扇出：每个终态恰好一次（真后处理需 Redis + worker，属部署窗） ─
        assert len(queue.enqueued) == RUNNING_TOTAL, (
            f"post_completion 扇出 {len(queue.enqueued)} ≠ {RUNNING_TOTAL}"
        )

        # ── 验收线 5/6：事实全部 ACK + 计数一致 ─────────────────────────────
        assert not driver["pending"], f"仍有未 ACK 的事实：{len(driver['pending'])}"
        assert counts["running"] == 0, f"仍有 RUNNING 残留：{counts['running']}"
        assert counts["terminal"] == counts["total"] == TOTAL_JOBS, counts["by_status"]
        assert run_terminal == counts["total"], (
            f"plan_run.terminal_job_count={run_terminal} ≠ 实重数 {counts['total']}"
        )
        assert run_aborted == counts["aborted"], f"aborted 计数漂移 {run_aborted}≠{counts['aborted']}"
        assert run_failed == counts["failed"], f"failed 计数漂移 {run_failed}≠{counts['failed']}"
        for host_id, terminal in host_counts.items():
            assert terminal == counts["per_host"][host_id], f"plan_run_host[{host_id}] 计数漂移"

        # ── 验收线 7：120s 内收敛 ───────────────────────────────────────────
        assert run_status in ("FAILED", "PARTIAL_SUCCESS", "SUCCESS"), f"未收敛：{run_status}"
        assert elapsed_total <= CONVERGENCE_BUDGET_SECONDS, f"收敛耗时 {elapsed_total:.1f}s"

        # ── 验收线 8：不依赖重启（同进程、同 app 实例、无人工干预） ──────────
        assert fastapi_app is not None
    finally:
        await _dispose_async_engine()
        _cleanup(seed)


# ── 验收线 3 判据自身的反例（纯函数，不起库）：判据必须对「没量到/量到失败」判红 ──────
def _samples(path: str, status: int, elapsed: float, n: int) -> list[dict]:
    return [{"path": path, "status": status, "elapsed": elapsed} for _ in range(n)]


_HB, _HEALTH = PROBE_ENDPOINTS[0][0], PROBE_ENDPOINTS[1][0]


@pytest.mark.parametrize(
    ("probe", "must_mention"),
    [
        pytest.param([], [_HB, _HEALTH], id="zero-samples"),
        pytest.param(
            _samples(_HB, 503, 5.0, 10) + _samples(_HEALTH, 503, 5.0, 10), ["503"], id="all-503-slow"
        ),
        pytest.param(
            _samples(_HB, 500, 0.01, 10) + _samples(_HEALTH, 200, 0.01, 10), [_HB], id="heartbeat-down"
        ),
        pytest.param(
            _samples(_HB, -1, 0.01, 10) + _samples(_HEALTH, 200, 0.01, 10), ["-1"], id="transport-errors"
        ),
        pytest.param(
            _samples(_HB, 200, 0.01, 2) + _samples(_HEALTH, 200, 0.01, 10), [_HB], id="too-few-samples"
        ),
        pytest.param(
            _samples(_HB, 200, 1.5, 10) + _samples(_HEALTH, 200, 0.01, 10), ["p99"], id="slow-but-200"
        ),
    ],
)
def test_probe_criterion_rejects_unmeasured_or_failing_probes(probe, must_mention):
    """#3247（A01）：这些输入在旧判据下全部「p99 < 1s 通过」（零样本回退 [0.0]、只取 200、两端点混算）。"""
    violations = _probe_violations(_probe_stats(probe))
    assert violations, "判据放行了失效探针"
    joined = "；".join(violations)
    for token in must_mention:
        assert token in joined, f"违规说明未指出 {token!r}：{joined}"


def test_probe_criterion_accepts_healthy_probes():
    probe = _samples(_HB, 200, 0.02, MIN_PROBE_SAMPLES_PER_ENDPOINT) + _samples(
        _HEALTH, 200, 0.01, MIN_PROBE_SAMPLES_PER_ENDPOINT
    )
    assert _probe_violations(_probe_stats(probe)) == []
