"""
Prometheus Metrics for Stability Test Platform

Exposes key metrics for monitoring and alerting.
"""

import functools
import logging
import os
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Try to import prometheus_client, fallback to mock if not available
try:
    from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

    # Mock classes for when prometheus_client is not installed
    class _MockTimer:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    class _MockMetric:
        """Mock metric that accepts any constructor args and supports method chaining"""
        def __init__(self, *args, **kwargs):
            pass
        def inc(self, *args, **kwargs):
            pass
        def dec(self, *args, **kwargs):
            pass
        def set(self, *args, **kwargs):
            pass
        def observe(self, *args, **kwargs):
            pass
        def labels(self, *args, **kwargs):
            return self
        def time(self):
            return _MockTimer()
        def info(self, *args, **kwargs):
            pass

    # Create mock classes that accept all Prometheus-specific kwargs
    class MockCounter(_MockMetric):
        pass

    class MockHistogram(_MockMetric):
        pass

    class MockGauge(_MockMetric):
        pass

    class MockInfo(_MockMetric):
        pass

    Counter = MockCounter
    Histogram = MockHistogram
    Gauge = MockGauge
    Info = MockInfo


def is_prometheus_available() -> bool:
    """Check if prometheus_client is available"""
    return PROMETHEUS_AVAILABLE


# ============================================================================
# Hot Update Metrics
# ============================================================================

# ADR-0040 D6（#1907）：热更新收敛结果计数——outcome ∈ deployed / converged /
# failed；entry 标注入口（ui_api / batch_direct / precheck_sync）。no-op 判定
# 与全量部署在同一 finalize 通道计数，drift = deployed 同义（desired != current
# 才部署），pending 经 host 视图 sync 状态表达，不另设。
hot_update_outcome_total = Counter(
    'stability_hot_update_outcome_total',
    'Total hot-update outcomes by entry and convergence result (ADR-0040 D6)',
    ['entry', 'outcome']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Device Lease Metrics
# ============================================================================

device_lease_released = Counter(
    'stability_device_lease_released_total',
    'Total number of device leases released',
    ['reason']  # completed, failed, timeout, canceled
) if PROMETHEUS_AVAILABLE else _MockMetric()

claim_lease_failed_total = Counter(
    'stability_claim_lease_failed_total',
    'Total claim attempts where acquire_lease returned None (device already leased)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

post_completion_enqueue_failed_total = Counter(
    'stability_post_completion_enqueue_failed_total',
    'Total post_completion_task SAQ enqueue failures on job terminal',
) if PROMETHEUS_AVAILABLE else _MockMetric()

rate_limiter_evicted_total = Counter(
    'stability_rate_limiter_evicted_total',
    'Total rate-limiter buckets evicted at capacity (high source cardinality signal)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

dashboard_summary_push_total = Counter(
    'stability_dashboard_summary_push_total',
    'Total coalesced dashboard_summary WS pushes (#2324)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

device_update_suppressed_total = Counter(
    'stability_device_update_suppressed_total',
    'DEVICE_UPDATE suppressed because update was immaterial (#2324)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Task Run Metrics
# ============================================================================

task_run_total = Counter(
    'stability_task_run_total',
    'Total number of task runs',
    ['status', 'task_type']
) if PROMETHEUS_AVAILABLE else _MockMetric()

task_run_state_changes = Counter(
    'stability_task_run_state_changes_total',
    'Total number of task run state changes',
    ['from_state', 'to_state']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Host Metrics
# ============================================================================

host_online = Gauge(
    'stability_host_online',
    'Number of online hosts',
    ['status']  # online, offline, degraded
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_heartbeat_missed = Counter(
    'stability_host_heartbeat_missed_total',
    'Total number of missed host heartbeats',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Device Metrics
# ============================================================================

device_online = Gauge(
    'stability_device_online',
    'Number of online devices',
    ['status']  # online, offline, busy
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Recycler Metrics
# ============================================================================

recycler_runs = Counter(
    'stability_recycler_runs_total',
    'Total number of recycler runs'
) if PROMETHEUS_AVAILABLE else _MockMetric()

recycler_timeouts = Counter(
    'stability_recycler_timeouts_total',
    'Total number of timeouts detected by recycler',
    ['timeout_type']  # dispatched, running, patrol_stall (ADR-0022 D10), host, device_lock
) if PROMETHEUS_AVAILABLE else _MockMetric()

recycler_duration = Histogram(
    'stability_recycler_duration_seconds',
    'Recycler execution duration in seconds',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Reconciler Metrics (ADR-0019 Phase 4a/4b)
# ============================================================================

reconciler_runs = Counter(
    'stability_reconciler_runs_total',
    'Total number of reconciler check invocations',
    ['check', 'outcome']  # check: expired_leases/stale_unknown/terminal_job_active_lease, outcome: success/error
) if PROMETHEUS_AVAILABLE else _MockMetric()

reconciler_actions = Counter(
    'stability_reconciler_actions_total',
    'Total number of actions taken by reconciler',
    ['action', 'reason']  # action: to_unknown/to_failed/release_lease, reason: lease_expired/unknown_grace_timeout/terminal_job_active_lease
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2287：本组随 ADR-0019 Phase 4a/4b 引入的两个**状态 gauge**
# （`stability_expired_active_leases{host_id}` = 宽限期持锁租约数、
# `stability_unknown_jobs{reason}` = UNKNOWN 态 job 数）自引入起**从未接线**，
# 零告警零看板消费者，定义已删除——状态转换由上面的 runs / actions 计数表达。
# 要恢复须「定义与接线同 PR」：全指标面生产者判据
# （tests/test_alert_metric_producers.py）会拦住只加定义不接线的改动。

# ============================================================================
# API Metrics
# ============================================================================

api_requests = Counter(
    'stability_api_requests_total',
    'Total number of API requests',
    ['method', 'endpoint', 'status_code']
) if PROMETHEUS_AVAILABLE else _MockMetric()

api_request_duration = Histogram(
    'stability_api_request_duration_seconds',
    'API request duration in seconds',
    ['method', 'endpoint'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# SAQ (Async Task Queue) Metrics
# ============================================================================

saq_tasks_total = Counter(
    'stability_saq_tasks_total',
    'Total SAQ tasks processed',
    ['task_name', 'status']  # status: completed, failed, aborted
) if PROMETHEUS_AVAILABLE else _MockMetric()

saq_task_duration = Histogram(
    'stability_saq_task_duration_seconds',
    'SAQ task execution duration in seconds',
    ['task_name'],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

saq_queue_depth = Gauge(
    'stability_saq_queue_depth',
    'Current SAQ queue depth',
    ['queue_name']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# SocketIO Metrics
# ============================================================================

socketio_connections = Gauge(
    'stability_socketio_connections_active',
    'Number of active SocketIO connections',
    ['namespace']  # /agent, /dashboard
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Background Thread Pool Metrics (#1122)
# ============================================================================

background_pool_queue_depth = Gauge(
    'stability_background_pool_queue_depth',
    'Background thread pool occupied slots (in-flight + queued)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

background_pool_rejected_total = Counter(
    'stability_background_pool_rejected_total',
    'Submissions rejected because the background pool queue was full',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# APScheduler Metrics
# ============================================================================

apscheduler_job_runs = Counter(
    'stability_apscheduler_job_runs_total',
    'Total APScheduler job executions',
    ['job_name', 'outcome']  # outcome: success, error
) if PROMETHEUS_AVAILABLE else _MockMetric()

apscheduler_job_duration = Histogram(
    'stability_apscheduler_job_duration_seconds',
    'APScheduler job execution duration in seconds',
    ['job_name'],
    buckets=[0.001, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# PlanRun / Patrol / Watcher Metrics (ADR-0020 / ADR-0021 / ADR-0022)
# ============================================================================

# PlanRun lifecycle
plan_run_terminal_total = Counter(
    'stability_plan_run_terminal_total',
    'Total PlanRuns reaching a terminal status',
    ['status']  # SUCCESS / PARTIAL_SUCCESS / FAILED
) if PROMETHEUS_AVAILABLE else _MockMetric()

plan_run_pass_rate = Histogram(
    'stability_plan_run_pass_rate',
    'Distribution of pass_rate at PlanRun terminal aggregation',
    ['status'],
    buckets=[0.0, 0.5, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

plan_run_aggregation_failed_total = Counter(
    'stability_plan_run_aggregation_failed_total',
    'PlanRun aggregation failures swallowed by recycler',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #77：counter_reconciler 对账发现并修复的计数器漂移（每漂移列一条）。
# 理论漂移率 = 0（所有终态入口都经 terminalization 集中服务）；> 0 即说明
# 有入口绕开集中服务或出现并发 race，SLO 守卫见 ADR-0026 §6。
# #1927：label 只保留 mode（白名单有界值域）——plan_run_id 单调无界，
# Python client 子序列永不回收，系统性漂移（正是本指标最需要工作的时刻）
# 会让基数爆炸；run 维度走 counter_reconciler 日志/审计。
plan_run_counter_drift_total = Counter(
    'stability_plan_run_counter_drift_total',
    'PlanRun terminalization counter drift repaired per drifted column',
    ['mode'],  # mode: total | terminal | completed | failed | aborted
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #703：abort 持锁时长 + DB 连接池占用（QueuePool 耗尽观测）
plan_run_abort_lock_seconds = Histogram(
    'stability_plan_run_abort_lock_seconds',
    'Wall time abort_plan_run holds PlanRun FOR NO KEY UPDATE before commit',
    ['phase'],  # abort_requested | finalize | admission
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0],
) if PROMETHEUS_AVAILABLE else _MockMetric()

db_pool_checked_out = Gauge(
    'stability_db_pool_checked_out',
    'SQLAlchemy QueuePool connections currently checked out',
    ['engine'],  # sync | async
) if PROMETHEUS_AVAILABLE else _MockMetric()

db_pool_overflow = Gauge(
    'stability_db_pool_overflow',
    'SQLAlchemy QueuePool current overflow (checked out beyond pool_size)',
    ['engine'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #1958：数据库侧检测到的死锁（SQLSTATE 40P01）。
# 动机：Job/Lease 锁序死锁曾持续复发约四周而平台侧**零指标零告警**——它只在
# PostgreSQL 服务端日志里可见，且受害事务可能被上层的通用 `except Exception`
# 吞掉（回收器的逐候选失败分支即如此，还被记成与原因不符的
# `reconciler_job_load_failed`），业务返回码完全看不出来。稳态为 0，任何增量
# 都意味着存在环路等待。#743/#729 的「只记日志无人盯」教训同样适用于数据库侧
# 错误类，故按**错误类**计数，而不是按某一条业务路径计数。
db_deadlock_total = Counter(
    'stability_db_deadlock_total',
    'Deadlocks detected by the database (SQLSTATE 40P01)',
    ['engine'],  # sync | async
) if PROMETHEUS_AVAILABLE else _MockMetric()

# 锁序修复（#1959/#1980/#1985/#2022）消掉了「环路等待」，但**代价会转移到普通等待**：
# 保留清理事务持有 plan_run 与候选子树行锁期间，热路径（complete / 批量续租）会在同一
# 批行上排队；反向亦然。这类等待对 `stability_db_deadlock_total` **完全不可见**——
# 只看死锁计数会得出「计数为 0 = 无代价」的错误结论（共享行加锁表的 Revisit 已登记）。
# 故单独观测「此刻有多少会话在等锁 / 等最久多久」，由 /metrics 拉取期现算
# （backend/api/routes/metrics.py 的 _refresh_lock_wait_gauges）。
db_lock_waiters = Gauge(
    'stability_db_lock_waiters',
    'PostgreSQL sessions currently waiting for a lock (wait_event_type=Lock)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

db_lock_wait_max_seconds = Gauge(
    'stability_db_lock_wait_max_seconds',
    'Age of the longest currently lock-waiting PostgreSQL query (0 when none)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# 保留清理的**持锁窗口**（从取第一把行锁到事务结束、锁释放）。它同时是
# 「#2022 只统一了顺序、没缩短窗口」这条 Revisit 的度量：NFS 目录回收仍在同一事务内
# （#1521/#1698 的「先文件后行」），窗口 ≈ 删除 + NFS 时间；窗口越长，上面的等待越久。
retention_txn_seconds = Histogram(
    'stability_retention_txn_seconds',
    'Wall time run_retention_cleanup holds row locks (plan_run + candidate subtree)',
    buckets=[0.05, 0.25, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# 「清理是否跟不上」的**可判定**信号（#2144）。候选数与被填满的批大小必须**成对**上报：
# `candidates >= batch_size` 表示本轮批被填满 = 队列里还有到期 run（持续成立即为积压）。
# 只报其一，判据就得在告警表达式里硬编码 `plan_run_retention_batch_size` —— 而它是
# **持锁窗口的杠杆**（#2105）、会被调小，硬编码的值会随调整失真。
retention_candidate_runs = Gauge(
    'stability_retention_candidate_runs',
    'Retention candidates seen by the last cleanup tick (bounded by the batch size)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

retention_batch_size = Gauge(
    'stability_retention_batch_size',
    'Configured retention cleanup batch size (plan_run_retention_batch_size)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ADR-0021 dispatch gate
dispatch_gate_runs_total = Counter(
    'stability_dispatch_gate_runs_total',
    'Total dispatch-gate runs (precheck → sync → re-verify)',
    ['outcome']  # passed / synced_passed / failed / skipped
) if PROMETHEUS_AVAILABLE else _MockMetric()

dispatch_gate_duration_seconds = Histogram(
    'stability_dispatch_gate_duration_seconds',
    'Wall-clock duration of the full dispatch gate per PlanRun',
    ['outcome'],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ADR-0022 patrol heartbeat / backoff
patrol_heartbeat_total = Counter(
    'stability_patrol_heartbeat_total',
    'Total patrol heartbeat aggregates received from agents',
    ['has_failures']  # 'true' if failed_delta > 0 else 'false'
) if PROMETHEUS_AVAILABLE else _MockMetric()

patrol_failure_streak_observed = Histogram(
    'stability_patrol_failure_streak_observed',
    'Distribution of current_failure_streak observed at heartbeat time',
    buckets=[0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20]
) if PROMETHEUS_AVAILABLE else _MockMetric()

patrol_manual_action_total = Counter(
    'stability_patrol_manual_action_total',
    'Total user-initiated manual interventions on patrol jobs',
    ['action']  # manual_retry / manual_exit
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ADR-0018 log signals (watcher)
log_signal_total = Counter(
    'stability_log_signal_total',
    'Total log signals ingested via /agent/log-signals',
    ['category']  # AEE / VENDOR_AEE / ANR / TOMBSTONE / MOBILELOG
) if PROMETHEUS_AVAILABLE else _MockMetric()

# dedup scan merge 静默跳过路径（#518 教训：配置缺失只记一条日志，没人盯 → 指标化）。
# 前两个「非零即告警」（稳态不该出现）；no_org_files / failed_plan_run 是预期路径
# （空产物自然跳过 / ADR-0028 显式门禁），只计数供阈值告警用。
merge_skip_tool_not_configured_total = Counter(
    'stability_merge_skip_tool_not_configured_total',
    'Total merge skips because scan tool not configured (STP_BACKEND_DEDUP_SCAN_* missing)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

merge_skip_no_org_files_total = Counter(
    'stability_merge_skip_no_org_files_total',
    'Total merge skips because no org files for the round (expected on empty rounds)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

merge_skip_failed_plan_run_total = Counter(
    'stability_merge_skip_failed_plan_run_total',
    'Total merge skips because plan run FAILED (ADR-0028 intended gate, not a defect)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #528 链接健康：unlinked_fixable 稳态不该出现。注意：唯一计算点是 GET
# watcher-summary 路由，计数随前端轮询重复自增——绝对值无意义，仅用于
# increase(...)>0 非零告警；不要据此推导「出现次数」。
unlinked_fixable_total = Counter(
    'stability_unlinked_fixable_total',
    'Total watcher-summary computations observing unlinked_fixable > 0 (GET-triggered)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# AEE db_history reconciler (Watcher 收编 AEE — D2 hash-skip / burst)
reconciler_skip_unchanged_total = Counter(
    'stability_reconciler_skip_unchanged_total',
    'Total AEE reconciler ticks skipped because db_history content hash was unchanged',
    ['host_id']  # 按 host 控基数,不打 job_id/serial
) if PROMETHEUS_AVAILABLE else _MockMetric()

reconciler_burst_mode_active = Gauge(
    'stability_reconciler_burst_mode_active',
    'Whether the AEE db_history reconciler is currently in burst mode (1) or baseline (0)',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

# Watcher capability 覆盖率（M4/T4-2 监控盘）：每个 Job 首次进入终态时按上报的
# watcher_capability 自增一次 → 覆盖率 = (inotifyd_root+inotifyd_shell+polling) / 总计。
watcher_capability_total = Counter(
    'stability_watcher_capability_total',
    'Total terminal jobs by reported watcher capability (once per job at first terminal)',
    ['capability']  # inotifyd_root | inotifyd_shell | polling | unavailable | skipped | unknown
) if PROMETHEUS_AVAILABLE else _MockMetric()

# Agent local outbox backlog (heartbeat extra)
agent_outbox_pending = Gauge(
    'stability_agent_outbox_pending',
    'Agent local outbox backlog depth reported via heartbeat',
    ['host_id', 'type'],  # terminal | log_signal
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# ADR-0026 P0 / P2 scale metrics (queue / renew / aggregation / concurrency)
# ============================================================================

admission_queue_latency_seconds = Histogram(
    'stability_admission_queue_latency_seconds',
    'Wall time from PlanRun.enqueued_at to PRECHECK→RUNNING admission',
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0],
) if PROMETHEUS_AVAILABLE else _MockMetric()

admission_queue_depth = Gauge(
    'stability_admission_queue_depth',
    'Number of PlanRuns currently in QUEUED status',
) if PROMETHEUS_AVAILABLE else _MockMetric()

lease_extend_batch_total = Counter(
    'stability_lease_extend_batch_total',
    'Total lease extend-batch item outcomes',
    ['outcome'],  # renewed | job_not_running | lease_missing | stale_token
) if PROMETHEUS_AVAILABLE else _MockMetric()

lease_extend_batch_size = Histogram(
    'stability_lease_extend_batch_size',
    'Number of lease items per extend-batch request',
    buckets=[1, 5, 10, 25, 50, 100, 200, 500],
) if PROMETHEUS_AVAILABLE else _MockMetric()

plan_run_aggregation_duration_seconds = Histogram(
    'stability_plan_run_aggregation_duration_seconds',
    'Duration of PlanRun terminal aggregation inside job_terminalization',
    ['path'],  # counters | full_scan
    buckets=[0.0005, 0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
) if PROMETHEUS_AVAILABLE else _MockMetric()

plan_run_devices_query_duration_seconds = Histogram(
    'stability_plan_run_devices_query_duration_seconds',
    'Wall time of GET /plan-runs/{id}/devices matrix endpoint',
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_operation_slots_held = Gauge(
    'stability_host_operation_slots_held',
    'OperationScheduler permits currently held (Agent-reported)',
    ['host_id'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_operation_slots_max = Gauge(
    'stability_host_operation_slots_max',
    'OperationScheduler max_concurrent_operations (Agent-reported)',
    ['host_id'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

host_operation_waiters = Gauge(
    'stability_host_operation_waiters',
    'OperationScheduler waiters queued for a permit (Agent-reported)',
    ['host_id'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Build Info
# ============================================================================

build_info = Info(
    'stability_build',
    'Build information'
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Security: CSRF Origin/Referer Middleware
# ============================================================================

csrf_rejected_total = Counter(
    'stability_csrf_rejected_total',
    'Total number of /api/v1/* unsafe requests rejected by CSRFOriginMiddleware',
    ['reason']  # origin_not_allowed | referer_not_allowed | missing_origin_and_referer
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Decorators and Utilities
# ============================================================================

def timed(metric: Histogram):
    """Decorator to time function execution"""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            if not PROMETHEUS_AVAILABLE:
                return func(*args, **kwargs)

            with metric.time():
                return func(*args, **kwargs)
        return wrapper
    return decorator


def record_socketio_connection(namespace: str, connected: bool):
    """Record SocketIO connection change (new framework metric)."""
    if not PROMETHEUS_AVAILABLE:
        return
    if connected:
        socketio_connections.labels(namespace=namespace).inc()
    else:
        socketio_connections.labels(namespace=namespace).dec()


def record_saq_task(task_name: str, status: str, duration: float):
    """Record a completed SAQ task with its outcome and duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    saq_tasks_total.labels(task_name=task_name, status=status).inc()
    saq_task_duration.labels(task_name=task_name).observe(duration)


def record_apscheduler_job(job_name: str, outcome: str, duration: float):
    """Record an APScheduler job execution with its outcome and duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    apscheduler_job_runs.labels(job_name=job_name, outcome=outcome).inc()
    apscheduler_job_duration.labels(job_name=job_name).observe(duration)


def record_api_request(method: str, endpoint: str, status_code: int, duration: float):
    """Record an API request.

    生产者**已落地**（#2286 收口 #1258 的过期陈述）：调用方是
    ``ApiRequestMetricsMiddleware``（``backend/core/request_metrics.py``），中间件由
    ``backend/main.py`` 的 ``add_middleware(ApiRequestMetricsMiddleware)`` 挂载；
    ``tests/test_alert_metric_producers.py`` 把这条 AST 追不到的接线钉成在场断言。

    仪表板目前**没有** API 请求/延迟面板——那是 #1258 当年「无生产者故撤面板」留下的
    缺口，前提已消失但面板未恢复。是否恢复、按什么维度恢复（错误率定义、分位选择、
    是否引入 ``$endpoint`` 变量）属面板设计判读，结论与再评估条件见
    ``docs/notes/testing/2026-09-16-grafana-unproduced-exemption-closure-2286.md``。
    基数纪律（endpoint 取路由模板而非原始路径）见 ``backend/core/request_metrics.py``。
    """
    if not PROMETHEUS_AVAILABLE:
        return

    api_requests.labels(
        method=method,
        endpoint=endpoint,
        status_code=str(status_code)
    ).inc()

    api_request_duration.labels(
        method=method,
        endpoint=endpoint
    ).observe(duration)


def record_plan_run_terminal(status: str, pass_rate: Optional[float] = None):
    """Record a PlanRun reaching a terminal status (ADR-0020 aggregation)."""
    if not PROMETHEUS_AVAILABLE:
        return
    plan_run_terminal_total.labels(status=status).inc()
    if pass_rate is not None:
        plan_run_pass_rate.labels(status=status).observe(max(0.0, min(1.0, pass_rate)))


def record_plan_run_aggregation_failed():
    """Record recycler swallowing a PlanRun aggregation error."""
    if not PROMETHEUS_AVAILABLE:
        return
    plan_run_aggregation_failed_total.inc()


def record_dispatch_gate(outcome: str, duration_seconds: float):
    """Record a single dispatch-gate run with its outcome and duration."""
    if not PROMETHEUS_AVAILABLE:
        return
    dispatch_gate_runs_total.labels(outcome=outcome).inc()
    dispatch_gate_duration_seconds.labels(outcome=outcome).observe(duration_seconds)


def record_patrol_heartbeat(failed_delta: int, current_failure_streak: int):
    """Record a patrol heartbeat aggregate from /agent/jobs/{id}/patrol-heartbeat."""
    if not PROMETHEUS_AVAILABLE:
        return
    patrol_heartbeat_total.labels(has_failures="true" if failed_delta > 0 else "false").inc()
    patrol_failure_streak_observed.observe(max(0, int(current_failure_streak or 0)))


def record_patrol_manual_action(action: str):
    """Record a user-initiated patrol manual_retry / manual_exit."""
    if not PROMETHEUS_AVAILABLE:
        return
    patrol_manual_action_total.labels(action=action).inc()


def record_log_signal_ingested(category: str):
    """Record one log signal accepted by the platform (ADR-0018)."""
    if not PROMETHEUS_AVAILABLE:
        return
    log_signal_total.labels(category=(category or "UNKNOWN").upper()).inc()


def record_reconciler_skip_unchanged(host_id: str, amount: int = 1):
    """Record AEE reconciler ticks skipped due to unchanged db_history hash (D2).

    amount 默认 1(Agent 进程内单次 tick 自增)。M0/Task2 桥接路径:后端在 Job 终态时
    一次性按整个 Job 生命周期累计的 ticks_skipped_unchanged 自增,把 Agent 进程内的
    本地计数搬到中心 /metrics(Agent 无独立 /metrics 暴露面)。
    """
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        n = int(amount)
    except (TypeError, ValueError):
        return
    if n <= 0:
        return
    reconciler_skip_unchanged_total.labels(host_id=str(host_id or "unknown")).inc(n)


def set_reconciler_burst_mode_active(host_id: str, active: bool):
    """Set AEE reconciler burst-mode gauge (1=burst / 0=baseline) for a host (D2)."""
    if not PROMETHEUS_AVAILABLE:
        return
    reconciler_burst_mode_active.labels(host_id=str(host_id or "unknown")).set(1 if active else 0)


def record_watcher_capability(capability: str):
    """Record a terminal job's reported watcher capability (M4/T4-2 coverage board).

    Called once per job at its first terminal transition (agent_api.complete_job,
    under the `not already_terminal` guard) so the counter stays monotonic and
    coverage = (inotifyd_root+inotifyd_shell+polling) / total.
    """
    if not PROMETHEUS_AVAILABLE:
        return
    cap = (capability or "unknown").strip() or "unknown"
    watcher_capability_total.labels(capability=cap[:32]).inc()


def record_agent_outbox_pending(host_id: str, outbox_type: str, count: int):
    """Update Agent outbox backlog gauge from heartbeat extra."""
    if not PROMETHEUS_AVAILABLE:
        return
    agent_outbox_pending.labels(
        host_id=str(host_id),
        type=outbox_type,
    ).set(max(0, int(count)))


def record_admission_queue_latency(seconds: float):
    """Record PlanRun queue wait (enqueued_at → admitted)."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    admission_queue_latency_seconds.observe(value)


def set_admission_queue_depth(depth: int):
    """Set current QUEUED PlanRun count."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        admission_queue_depth.set(max(0, int(depth)))
    except (TypeError, ValueError):
        return


def record_lease_extend_batch(outcomes: Dict[str, int], batch_size: int):
    """Record extend-batch item outcomes and request size."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        lease_extend_batch_size.observe(max(0, int(batch_size)))
    except (TypeError, ValueError):
        pass
    for outcome, count in (outcomes or {}).items():
        try:
            n = int(count)
        except (TypeError, ValueError):
            continue
        if n <= 0:
            continue
        lease_extend_batch_total.labels(outcome=str(outcome)[:64]).inc(n)


def record_plan_run_aggregation_duration(seconds: float, path: str):
    """Record terminalization aggregation duration (counters | full_scan)."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    plan_run_aggregation_duration_seconds.labels(
        path=(path or "unknown")[:32],
    ).observe(value)


def record_plan_run_abort_lock_seconds(seconds: float, phase: str):
    """#703：abort 持 PlanRun 行锁的墙钟时间（按阶段）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    plan_run_abort_lock_seconds.labels(phase=(phase or "unknown")[:32]).observe(value)


# 漂移列白名单（ADR-0026 §6 五计数器；未知列名不得进入 label 值域）
_PLAN_RUN_COUNTER_MODES = ("total", "terminal", "completed", "failed", "aborted")


def record_plan_run_counter_drift(plan_run_id: int, modes: "list[str] | tuple[str, ...]"):
    """#77：记录 counter_reconciler 修复的计数器漂移（每漂移列一条）。

    ``modes`` 为漂移列短名（total/terminal/completed/failed/aborted）；
    非白名单值过滤掉——防未来新增列悄然扩 label 值域。
    #1927：run 维度不进 label（无界基数），仅记日志。
    """
    if not PROMETHEUS_AVAILABLE:
        return
    valid_modes = [m for m in (modes or ()) if m in _PLAN_RUN_COUNTER_MODES]
    if not valid_modes:
        return
    for mode in valid_modes:
        plan_run_counter_drift_total.labels(mode=mode).inc()
    logger.info(
        "plan_run_counter_drift_repaired plan_run_id=%s modes=%s",
        plan_run_id, ",".join(valid_modes),
    )


def record_db_pool_status(engine_label: str, *, checked_out: int, overflow: int):
    """#703：刷新 QueuePool 占用 Gauge（checkout/checkin 事件驱动）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    label = (engine_label or "unknown")[:16]
    try:
        db_pool_checked_out.labels(engine=label).set(max(0, int(checked_out)))
        db_pool_overflow.labels(engine=label).set(max(0, int(overflow)))
    except (TypeError, ValueError):
        return


def record_db_deadlock(engine_label: str) -> None:
    """#1958：按引擎累计数据库检测到的死锁（SQLSTATE 40P01）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    label = (engine_label or "unknown")[:16]
    try:
        db_deadlock_total.labels(engine=label).inc()
    except (TypeError, ValueError):
        return


def record_db_lock_waiters(waiters: int, max_wait_seconds: float) -> None:
    """#2104：锁等待快照（/metrics 拉取期采样 pg_stat_activity）。观测面不得抛。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        db_lock_waiters.set(max(0, int(waiters)))
        db_lock_wait_max_seconds.set(max(0.0, float(max_wait_seconds)))
    except (TypeError, ValueError):
        return


def record_retention_txn(seconds: float) -> None:
    """#2104：保留清理的持锁窗口（一次 tick 一个观测）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    retention_txn_seconds.observe(value)


def record_retention_candidates(candidates: int, batch_size: int) -> None:
    """#2144：本轮候选数 + 配置批大小（成对，判据见上面的 gauge 注释）。

    每次 tick 都上报（**含候选为 0 的那次**）：否则 gauge 会停在上一轮的非零值上，把
    「已清空」显示成「仍在积压」——一条会骗人的观测面比没有更糟。
    """
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        seen = max(0, int(candidates))
        limit = max(0, int(batch_size))
    except (TypeError, ValueError):
        return
    retention_candidate_runs.set(seen)
    retention_batch_size.set(limit)


def record_plan_run_devices_query_duration(seconds: float):
    """Record device-matrix endpoint wall time."""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    plan_run_devices_query_duration_seconds.observe(value)


def record_host_operation_concurrency(
    host_id: str, *, held: int, max_slots: int, waiting: int,
):
    """Update per-host OperationScheduler concurrency gauges from heartbeat."""
    if not PROMETHEUS_AVAILABLE:
        return
    hid = str(host_id or "unknown")
    try:
        host_operation_slots_held.labels(host_id=hid).set(max(0, int(held)))
        host_operation_slots_max.labels(host_id=hid).set(max(0, int(max_slots)))
        host_operation_waiters.labels(host_id=hid).set(max(0, int(waiting)))
    except (TypeError, ValueError):
        return


def get_metrics_response():
    """Generate Prometheus metrics response"""
    if not PROMETHEUS_AVAILABLE:
        return b"# Prometheus client not installed\n", "text/plain"

    return generate_latest(), CONTENT_TYPE_LATEST


def init_build_info(version: str = "unknown", commit: str = "unknown"):
    """Initialize build info metrics（值由 #2341 的 ``resolve_build_info()`` 提供）。

    ``version`` / ``commit`` 的默认值刻意是 ``unknown``：真值来自部署树的
    ``release-manifest.json``，读不到就显式回落——**不回落任何具体版本号**，
    否则又会造出「看起来有答案」的假信息（本单要消灭的正是那个形态）。

    **多进程模式不导出**（prometheus_client 官方约束：「Info metrics do not work in
    multiprocess mode」）：当前 systemd 单元是单进程 uvicorn，故 ``Info`` 可用；
    若将来引入 ``--workers`` + ``PROMETHEUS_MULTIPROC_DIR``，本指标会**静默消失**，
    必须换成带 label 的 ``Gauge('stability_build_info', ..., ['version', 'commit'])``。
    这里做一条运行期自检，免得下一个人靠踩发现。
    """
    if PROMETHEUS_AVAILABLE and os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        logger.warning(
            "build_info_multiprocess_mode_unsupported — Info 指标在多进程模式下不导出，"
            "请改用带 label 的 Gauge（见 init_build_info docstring）",
        )
    if PROMETHEUS_AVAILABLE:
        build_info.info({
            'version': version,
            'commit': commit,
        })
