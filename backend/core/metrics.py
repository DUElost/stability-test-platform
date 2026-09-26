"""
Prometheus Metrics for Stability Test Platform

Exposes key metrics for monitoring and alerting.
"""

import functools
import logging
import os
from typing import Callable, Dict, Any

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

# #3082：链触发 settle 窗的评估结局——区分「窗内未就绪跳过」与「就绪提前放行」。
# 存在理由：#2755 的固定窗让「父段已结束但仍在等窗」与「链断了」在观测面上不可分辨；
# outcome 词表（settling_skipped | early_release）让提前放行率可直接被 PromQL 问出来
# （放行率 ≈0 = 就绪判据过严；≈1 且 init 失败率回升 = 判据过松，回退开关见 settings）。
plan_chain_settle_outcome_total = Counter(
    'stability_plan_chain_settle_outcome_total',
    'Outcomes of the chain-trigger settle window evaluation (#3082)',
    ['outcome'],  # settling_skipped | early_release
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

# #3219: Agent-local cumulative histograms mirrored from heartbeat.extra at
# scrape time. They are Gauges because the Agent owns the counts and may restart;
# use rate(bucket[window]) for quantiles. Vocabulary is fixed by
# backend.agent.contracts.heartbeat_timing; no device label is accepted.
agent_heartbeat_phase_bucket = Gauge(
    'stability_agent_heartbeat_phase_seconds_bucket',
    'Cumulative Agent heartbeat tick duration buckets, mirrored per host',
    ['host_id', 'phase', 'le'],
) if PROMETHEUS_AVAILABLE else _MockMetric()
agent_heartbeat_phase_sum = Gauge(
    'stability_agent_heartbeat_phase_seconds_sum',
    'Cumulative Agent heartbeat tick duration seconds, mirrored per host',
    ['host_id', 'phase'],
) if PROMETHEUS_AVAILABLE else _MockMetric()
agent_heartbeat_phase_count = Gauge(
    'stability_agent_heartbeat_phase_seconds_count',
    'Cumulative Agent heartbeat tick observations, mirrored per host',
    ['host_id', 'phase'],
) if PROMETHEUS_AVAILABLE else _MockMetric()
agent_heartbeat_probe_due = Gauge(
    'stability_agent_heartbeat_probe_due',
    'Devices due for a slow or disk probe in the last Agent tick',
    ['host_id', 'kind'],  # slow | disk
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Device Metrics
# ============================================================================

device_online = Gauge(
    'stability_device_online',
    'Number of online devices',
    ['status']  # online, offline, busy
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2754：fleet 级 device_online{status} 只有总数，**看不见「单台 host 的设备批量掉线」**——
# 2026-09-18 host .81 的实测形态正是这样：host 状态 ONLINE、心跳新鲜、mount ok，
# 而它 16 台里 15 台 adb offline，平台上零告警。这里按 host 暴露 adb_state 分桶计数，
# 让「谁掉线了」与「是不是波次（相对自身近 45m 的峰值）」都能被 PromQL 表达。
# 分桶是**封闭词表**（见 api/routes/metrics.py 的 _ADB_STATE_BUCKETS）：adb 的原始状态是
# 自由字符串（`no permissions`、空串、版本差异词都可能），不归桶就会把 series 基数交给运气。
host_device_adb_state = Gauge(
    'stability_host_device_adb_state',
    'Devices per host by adb_state bucket (control-plane DB view)',
    ['host_id', 'state'],  # state: device | offline | unauthorized | other
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2900/#2957：Agent 已经算出的 host 健康 reason 此前**只落进 `host.extra` 这个 JSON
# 就停了**——`deploy/prometheus/alerts-stability-platform.yml` 全文没有任何 expr 引用
# 它们，于是「xHCI 主控死亡致整机 USB 全盲」（fleet 三例、最长 11 天零告警）在告警面
# 上不存在。本 gauge 把 reason 词表折成 per-host 0/1 series，使「有 reason」第一次
# 可被 PromQL 问出来。词表是**封闭**的（见 api/routes/metrics.py 的 _HEALTH_REASONS，
# 由 tests/test_host_health_reason_surface.py 绑回 agent 源码），否则告警选择器漏一个
# 值就静默不告（#1257 的不存在标签选择器、#1958 的四周零指标同族）。
host_health_reason = Gauge(
    'stability_host_health_reason',
    'Host health reasons reported by the agent heartbeat (1 = present)',
    ['host_id', 'reason'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2957：上面那些 USB 判据**能不能真的读到内核日志**。Agent 以 `User=android` 运行、
# 不在 adm/systemd-journal 组，实测非特权 `journalctl -k` 退出码 0 且 stdout 只有
# `-- No entries --`——与「内核干净」同形；`dmesg_restrict=1` 又把 /dev/kmsg 与 dmesg
# 两条备用路都堵死（本机实测 open 报 EPERM）。所以 reason 全 0 有两种截然相反的成因：
# 「查过且干净」与「根本没查过」。本 gauge 把后者单独暴露，使「绿而空」不再是
# 一种无法区分的状态。三态词表见
# backend/agent/contracts/kernel_usb_faults.py 的 CHANNEL_STATES。
host_kernel_log_channel = Gauge(
    'stability_host_kernel_log_channel',
    'Agent kernel-log channel availability per host (exactly one state = 1)',
    ['host_id', 'state'],  # state: ok | unavailable | unknown
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Script Presence Metrics（#2958 第五道闸）
# ============================================================================

# #2958：host × 脚本目标版本的**在位矩阵**。存在理由与本文件其它 per-host 面同型：
# agent 侧核验（`verify_scripts` RPC）只在**派发时**覆盖「本 run 的 host × 本 run
# 快照」——维护窗 / 近期无 run 的 host 无账（`.89` 缺 3 个版本目录而 DB 面全绿）。
# 常设 sweep（每日）把结果落 `host_script_presence`，本 gauge 把它折成 per-host ×
# **闭词表六态**的 series，使「哪台缺什么」第一次可被 PromQL 问出来。
# 口径（与 `services/script_presence.PRESENCE_STATES` 绑定，新增态必须同步）：
#   present / missing / mismatch / unknown / n_a / maintenance
# `unknown`（agent 不可达）**不是绿**；`n_a` 是「该 host 不会跑到」不判红；
# `maintenance` 是维护窗内的缺口（不判红，归队前补分发由流程盯）。
host_script_presence = Gauge(
    'stability_host_script_presence',
    'Host script presence rows per state (control-plane DB view)',
    ['host_id', 'state'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #3222：fleet 包模式计数——`/metrics` 拉取期从 host.script_packages_mode 列现算
# （routes/metrics.py `_refresh_script_packages_mode_gauge`，与 summary fleet_packages 同源；
# #3315 前曾在 sweep 内按本轮切片 set，单机 refresh 会覆盖 fleet 聚合、重启后缺席到下次 cron）。
# ADR-0051「fleet 全 strict」的机器不变量：期望 {mode="package"} == 在册 host 数，
# tree/mixed 出现 = 有主机仍在（已不存在的）tree 语义或半旧代码，unknown = 列为 NULL
# （从未核过，或最近一次核验不可判）。
host_script_packages_mode = Gauge(
    'stability_host_script_packages_mode',
    'Host count by script package mode (ADR-0051 #3222)',
    ['mode'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2958：账本新鲜度 = 最近一次**完整** sweep 里最旧一行的观测时刻（unix 秒）。
# 缺了它，「探针死了」与「全在位」不可分辨——#2900/#2984 的同族教训（绿而空）。
script_presence_sweep_timestamp = Gauge(
    'stability_script_presence_sweep_timestamp',
    'Oldest checked_at of the last complete script presence sweep (unix seconds)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2983：控制面 SSH 探针连续窗 strike（host.extra.health_probe.strike_open）。
# 1 = 连续 N 轮 AGENT_MUTE；0 = 未开。差集清理与其它 per-host gauge 同族。
host_health_probe_strike = Gauge(
    'stability_host_health_probe_strike',
    'Control-plane host health probe consecutive AGENT_MUTE strike (1=open)',
    ['host_id'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ============================================================================
# Risk Classification Metrics
# ============================================================================

# #2365：风险分级的**覆盖率**观测。此前「风险分布长期只有未知」无法与「判据坏了」
# 区分——仪表盘卡片两种状态长得一样。这里按最近一次 `/results/summary` 计算的结果
# 暴露各桶 job 数：`s+a+b` = 有异常信号、可判定的 job；`unknown` = 该窗口内没有任何
# 异常事件的 job（**不是**「低风险」，是「无判定依据」）。
# ADR-0045 D2：标签值随对外词表一起收敛成级别本身（原 `high/medium/low`）——
# 指标标签与 API 字段两套词，正是 #2494 那张"四面四形状"表里的一格。
risk_jobs_by_level = Gauge(
    'stability_risk_jobs_by_level',
    'Risk-classified job count by level (last /results/summary computation)',
    ['level']  # s, a, b, unknown（ADR-0045 D2；原 high/medium/low）
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

# #2531：UNKNOWN 积压读数（**定义与接线同 PR**——#2287 删掉的两只同族 gauge
# `stability_expired_active_leases` / `stability_unknown_jobs` 就是死在「只有定义、
# 没人写」上；生产者判据 tests/test_alert_metric_producers.py 会拦住只加定义的改动）。
# producer: device_lease_reconciler._record_unknown_backlog（每个 reconcile tick 写一次）。
# 为什么必须有：Phase 2 的收口速率是离散的（一轮最多 ``RECONCILER_DRAIN_BATCH`` 台），
# 「还有多少台卡在 UNKNOWN、还要多久」在修前只能靠翻日志数事件；`reconciler_actions`
# 是增量计数器，读不出积压。
reconciler_unknown_backlog = Gauge(
    'stability_reconciler_unknown_backlog',
    'Jobs still in UNKNOWN at the end of a lease-reconcile tick, by grace state',
    ['state']  # state: grace_expired/within_grace/missing_ended_at（基数恒定 3）
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

# ADR-0052 D4（#3244）恢复路径观测。pending_depth 由 counter_reconciler sweep
# 每轮 set（非实时——实时深度看聚合任务日志 plan_run_aggregated）；持续 > 0
# 说明唤醒入队失败或 SAQ 停摆，正常应为 0 或短暂尖峰。replayed_total 只在
# 修复路径触发：pending_drain=唤醒丢失重放，terminal_effects=终态副作用块
# 崩溃窗口重放。实施验收（§5-③ 120s 收敛）后两者应保持 0。
plan_run_pending_aggregation_depth = Gauge(
    'stability_plan_run_pending_aggregation_depth',
    'plan_run_pending_aggregation backlog rows (ADR-0052 recovery sweep gauge)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

plan_run_aggregation_replayed_total = Counter(
    'stability_plan_run_aggregation_replayed_total',
    'ADR-0052 recovery replays by the counter_reconciler sweep',
    ['kind'],  # kind: pending_drain | terminal_effects
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

# ── #703 第 3 面：把「池饱和」从**事后翻日志**变成**事前可见** ─────────────────────
# 上面两个 gauge 只有**状态**（当下借出多少），看不见**等待**：#703 的现场形态是
# `QueuePool limit of size 30 overflow 60 ...` 把 app 池与 PG max_connections 同时顶满，
# 而这两条 gauge 在超时那一刻只是「已经满了」，与「忙但正常」同形。
# 补两条**事件侧**序列（同一位置埋：`Pool.connect()` 是全仓借连接的唯一入口）：
#   * `_seconds`：拿到一条连接要多久（= 排队等待 + 建连 + pre-ping，HELP 里写明含后两者，
#     别把它读成纯排队时间）；池将满时它的尾部会先顶到 pool_timeout。
#   * `_failures_total{kind}`：借不到连接。`timeout` = 池耗尽（正是 #703 的那一下），
#     `error` = DBAPI/驱动失败（与 #1958 的死锁计数不重叠：那条在 checkout 之后）。
# 容量取向（pool_size/overflow/pool_timeout 该是多少）**不在本指标里回答**——那是 #703
# 第 2 面，方向级取舍需 ADR；这里只保证「触顶这件事在指标上留得下痕」。
db_pool_checkout_seconds = Histogram(
    'stability_db_pool_checkout_seconds',
    'Time to acquire a pooled connection: queue wait + connect + pre-ping '
    '(installed on Pool.connect by backend/core/database.py)',
    ['engine'],  # sync | async
    buckets=[0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
) if PROMETHEUS_AVAILABLE else _MockMetric()

db_pool_checkout_failures_total = Counter(
    'stability_db_pool_checkout_failures_total',
    'Pool checkout failures by class (timeout=pool queue wait, slots_exhausted=PG '
    'rejected new connection, error=other DBAPI/driver)',
    ['engine', 'kind'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #703 / #1880：一次 abort 的**扇出规模**。#1880 的形态是「单台 host 热更新」实际终态化了
# 整轮 run——作用域错位只有换算出「多少 job」才看得见，而当时既无日志聚合也无指标。
# 按 `scope`（run | host）分开看是判据本身：host 侧出现 run 量级的扇出，就是那次错位复发。
plan_run_abort_fanout_jobs = Histogram(
    'stability_plan_run_abort_fanout_jobs',
    'Jobs touched by one abort_plan_run call (terminalized + control-signalled), by scope',
    ['scope'],  # run | host
    buckets=[0, 1, 2, 5, 10, 25, 50, 100, 250, 500, 1000],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# ── ADR-0047 D2（#2959）：终态请求（/complete）的独立并发舱壁 ────────────────
# R523 现场：490 个 RUNNING 同时回传终态，峰值 55 req/s、波内 1644 次请求，每个请求
# 在第一次 DB 查询就占一条连接并争同一条 plan_run 行 ⇒ 池被抽干。舱壁把「同时执行的
# 终态请求」限到 N（默认 16），**排队发生在连接池之外**；等不到就快失败 503。
# inflight/waiting 是水位（诊断用），rejected 与 wait_seconds 是事件侧（告警用）。
terminal_bulkhead_inflight = Gauge(
    'stability_terminal_bulkhead_inflight',
    'Terminal (/complete) requests currently holding a bulkhead slot',
) if PROMETHEUS_AVAILABLE else _MockMetric()

terminal_bulkhead_waiting = Gauge(
    'stability_terminal_bulkhead_waiting',
    'Terminal requests waiting for a bulkhead slot (queue is outside the DB pool)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

terminal_bulkhead_rejected_total = Counter(
    'stability_terminal_bulkhead_rejected_total',
    'Terminal requests rejected by the bulkhead (waited past the wait budget)',
) if PROMETHEUS_AVAILABLE else _MockMetric()

terminal_bulkhead_wait_seconds = Histogram(
    'stability_terminal_bulkhead_wait_seconds',
    'Time a terminal request waited for a bulkhead slot (including the rejected ones)',
    buckets=[0.0005, 0.001, 0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
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

# #2741 / ADR-0049：audit_logs 分层保留期裁剪计数（按层分桶：session/business/
# security）。与上面的 stability_retention_* 家族（PlanRun）平行——两族判据不同
# （行锁窗口 vs 纯时间分层），不共用指标。裁剪的治理事实另落一条汇总审计
# （audit_retention_pruned，见 backend/scheduler/audit_log_cleanup.py）。
audit_retention_pruned_total = Counter(
    'stability_audit_retention_pruned_total',
    'audit_logs rows pruned by layered retention, by layer (#2741)',
    ['layer'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

# #2316：孤儿 DLE 清理的**跳过**计数（按原因分桶）。被跳过的行既不删行也不推进批头，
# 而它们恒为最老 → 积压到批大小后 `purged` 恒为 0；此前只有 warning，积压不可观测。
# 取值：root_unset / path_invalid / purge_failed（与 `dle_orphan_skipped_*` 日志锚点同名）。
dle_orphan_skipped_total = Counter(
    'stability_dle_orphan_skipped_total',
    'Orphan DeviceLogEvent cleanup rows skipped, by reason (#2316)',
    ['reason'],
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

# #2394①③ UNISOC「落成未采到」可见面（job 终态桥接，host 维度——沿用 skip_unchanged
# 的控基数约定，不打 serial/job）。abandoned/oversized 为单调累计；unresolved 是
# 该 host 最近一个完成 UNISOC job 的末拍快照；present 由任意平台 reconciler 成功
# 回报后置 1——UNISOC 设备所在 host 长期无 present 样本 = C7/C8 盲区信号。
reconciler_dirs_abandoned_total = Counter(
    'stability_reconciler_dirs_abandoned_total',
    'UNISOC event dirs abandoned after MAX_DIR_ATTEMPTS consecutive pull failures (job-completion bridge)',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

reconciler_dirs_oversized_skipped_total = Counter(
    'stability_reconciler_dirs_oversized_skipped_total',
    'UNISOC event dirs degraded to metadata-only by the size guard (#2252); '
    'per-job unique dirs, summed once per completed job (NOT distinct dirs across jobs)',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

reconciler_unresolved_dirs = Gauge(
    'stability_reconciler_unresolved_dirs',
    'UNISOC dirs listed on device but unresolved at the last completed job (final-tick snapshot per host)',
    ['host_id']
) if PROMETHEUS_AVAILABLE else _MockMetric()

watcher_reconciler_present = Gauge(
    'stability_watcher_reconciler_present',
    'Last completed job on this host reported a running platform reconciler (1), labeled by platform',
    ['host_id', 'platform']
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

# #3217：crash artifact 投递（heartbeat extra；Agent 进程级累计，重启清零）。
# 用 Gauge 承载 Agent 上报的累计值、在 PromQL 里按计数器语义读（increase() 把回落当作
# 重置）。不在控制面算差值再 inc 一个 Counter：差值要记「上一次看到的值」，控制面重启
# 就丢；多实例时心跳分流到不同实例，还会被重复累加。名字不带 _total（Counter 的保留后缀）。
agent_artifact_submits = Gauge(
    'stability_agent_artifact_submits',
    'Crash artifact submissions since Agent process start, reported via heartbeat '
    '(resets on Agent restart: read with increase())',
    ['host_id'],
) if PROMETHEUS_AVAILABLE else _MockMetric()

agent_artifact_dropped = Gauge(
    'stability_agent_artifact_dropped',
    'Crash artifacts lost by the Agent uploader since process start, by stage '
    '(submit=queue full/not running/bad payload, promote=shared-root promote failed, '
    'post=registration POST failed); heartbeat-reported, resets on Agent restart',
    ['host_id', 'stage'],  # submit | promote | post
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

# #2909 问题③：周期回归链的覆盖差可发现化（拉取期现算于 api/routes/metrics.py）。
# kind 是封闭词表：online_total=fleet 未退役 host 上的 ONLINE 设备数；
# scheduled_union=全部 enabled task_schedule 存储清单的并集大小（含已掉线台，
# 反映清单存量）；gap_missing=ONLINE ∉ 任何 schedule 清单（「20% 断了没人知道」
# 的那个量）。ratio 由 PromQL 现算，指标只落原子事实。
chain_coverage_devices = Gauge(
    'stability_chain_coverage_devices',
    'Periodic-regression chain coverage facts computed at scrape time '
    '(kind=online_total|scheduled_union|gap_missing) (#2909)',
    ['kind'],
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


def record_plan_run_terminal(status: str):
    """Record a PlanRun reaching a terminal status (ADR-0020 aggregation).

    ADR-0048：`stability_plan_run_pass_rate` histogram 已退役——通过率不再是
    run 语义的一部分；设备失败台数由 job/run 计数器与结果层承载。
    """
    if not PROMETHEUS_AVAILABLE:
        return
    plan_run_terminal_total.labels(status=status).inc()


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


def _pos_int_or_none(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def record_reconciler_dirs_abandoned(host_id: str, amount: int):
    """#2394: job 终态桥接——本 job 放弃目录数（单调累计，>0 才计）。"""
    n = _pos_int_or_none(amount)
    if n is None or not PROMETHEUS_AVAILABLE:
        return
    reconciler_dirs_abandoned_total.labels(host_id=str(host_id or "unknown")).inc(n)


def record_reconciler_dirs_oversized_skipped(host_id: str, amount: int):
    """#2394/#2252: job 终态桥接——本 job 内按名去重后的降级目录数（>0 才计）。

    口径（#2640）：`amount` 来自 `UnisocReconciler._oversized_seen`，那个集合**随
    reconciler 实例（每 job 一份）新建**，所以唯一性只在 job 内成立。同一目录在 N 个
    job 里被降级就会累加 N 次——因此本指标不能当「某 host 有多少个被降级目录」读
    （那会高估），要看目录数需按 job 取末拍快照。
    """
    n = _pos_int_or_none(amount)
    if n is None or not PROMETHEUS_AVAILABLE:
        return
    reconciler_dirs_oversized_skipped_total.labels(host_id=str(host_id or "unknown")).inc(n)


# ============================================================================
# #2873：per-host 推式 Gauge 的 label child 差集清理
# ============================================================================
# prometheus_client 的 label child 一旦创建就**常驻 registry**：host 退役/移出
# 在册后「停止刷新」= 把最后一个值（往往是故障值）永久冻结——#2791 为拉取侧的
# adb_state gauge 修过同型问题，本文件这 7 组推式 per-host gauge 是它的补集。
# 写点全部收口在本文件（心跳/完成回报/OperationScheduler 桥接），故在此记账：
# 每次 set 前登记 (gauge, host_id, 其余 label 值)；/metrics 拉取端调用
# `sweep_stale_host_gauge_children(live)` 对不在 live 集的 host remove 全部 child。
# live 口径=「在册（retired_at IS NULL）」，与 issue 判据同源：短暂掉线的 host
# 其末值仍有操作意义，只清「不再是容量」的（ADR-0038 D5 语义）。
# 前提：所有受影响 gauge 的 labelnames 以 host_id 打头（remove 为位置参数）。
_PUSH_HOST_GAUGES: dict[int, Any] = {}
_PUSH_HOST_CHILDREN: dict[int, dict[str, set[tuple[str, ...]]]] = {}


def _note_host_child(gauge: Any, host_id: str, rest: tuple[str, ...]) -> None:
    _PUSH_HOST_GAUGES[id(gauge)] = gauge
    _PUSH_HOST_CHILDREN.setdefault(id(gauge), {}).setdefault(host_id, set()).add(rest)


def sweep_stale_host_gauge_children(live_host_ids: set[str]) -> int:
    """移除不再在册 host 的全部 child metric；返回移除数（/metrics 拉取端调用）。"""
    removed = 0
    for gid, per_host in _PUSH_HOST_CHILDREN.items():
        gauge = _PUSH_HOST_GAUGES[gid]
        for host_id in [h for h in per_host if h not in live_host_ids]:
            for rest in per_host.pop(host_id):
                try:
                    gauge.remove(host_id, *rest)
                except KeyError:
                    pass  # child 已被其它路径清掉：语义上已达成
                removed += 1
    return removed


def set_reconciler_unresolved_dirs(host_id: str, value: int):
    """#2394: 末拍 unresolved 快照（0 也有意义=最近 job 全收敛，照写）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        n = int(value)
    except (TypeError, ValueError):
        return
    hid = str(host_id or "unknown")
    _note_host_child(reconciler_unresolved_dirs, hid, ())
    reconciler_unresolved_dirs.labels(host_id=hid).set(max(0, n))


def set_watcher_reconciler_present(host_id: str, platform: str):
    """#2394③: reconciler 在位事实（platform 段做标签，控制基数）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    plat = str(platform or "").strip().upper() or "UNKNOWN"
    _note_host_child(watcher_reconciler_present, str(host_id or "unknown"), (plat,))
    watcher_reconciler_present.labels(
        host_id=str(host_id or "unknown"), platform=plat,
    ).set(1)


def set_reconciler_burst_mode_active(host_id: str, active: bool):
    """Set AEE reconciler burst-mode gauge (1=burst / 0=baseline) for a host (D2)."""
    if not PROMETHEUS_AVAILABLE:
        return
    hid = str(host_id or "unknown")
    _note_host_child(reconciler_burst_mode_active, hid, ())
    reconciler_burst_mode_active.labels(host_id=hid).set(1 if active else 0)


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
    _note_host_child(agent_outbox_pending, str(host_id), (str(outbox_type),))
    agent_outbox_pending.labels(
        host_id=str(host_id),
        type=outbox_type,
    ).set(max(0, int(count)))


#: #3217：心跳 extra 键 → `stability_agent_artifact_dropped` 的 stage 标签值。
AGENT_ARTIFACT_DROP_KEYS = (
    ("submit", "artifact_dropped_submit_total"),
    ("promote", "artifact_dropped_promote_total"),
    ("post", "artifact_dropped_post_total"),
)
AGENT_ARTIFACT_SUBMITS_KEY = "artifact_submits_total"


def _nonneg_int_or_none(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def record_agent_artifact_upload(host_id: str, extra: dict) -> None:
    """#3217：心跳 extra 里的 artifact 投递累计量 → 两个 per-host Gauge。

    只认显式键；缺键或非法值的那一项**跳过、不写 0**——旧 Agent 不带这些键，写 0 会让
    升级窗口里的旧主机被读成「零丢失」（没量到 ≠ 没发生）。
    """
    if not PROMETHEUS_AVAILABLE or not isinstance(extra, dict):
        return
    hid = str(host_id)
    submits = _nonneg_int_or_none(extra.get(AGENT_ARTIFACT_SUBMITS_KEY))
    if submits is not None:
        _note_host_child(agent_artifact_submits, hid, ())
        agent_artifact_submits.labels(host_id=hid).set(submits)
    for stage, key in AGENT_ARTIFACT_DROP_KEYS:
        dropped = _nonneg_int_or_none(extra.get(key))
        if dropped is not None:
            _note_host_child(agent_artifact_dropped, hid, (stage,))
            agent_artifact_dropped.labels(host_id=hid, stage=stage).set(dropped)


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


def _safe_emit(emit) -> None:
    """#703 残留②：**观测回写不得把异常递给业务调用栈**。

    abort 族的观测点全部落在 ``db.commit()`` **之后**的返回路径上——那一刻业务
    结果已定，prometheus_client 内部任何异常（注册竞态、label 处理、multiprocess
    模式 IO）若照常传播，调用方看到的就是「一次成功 abort 返回 500」。
    借还侧早已用同款包裹（``database._record_pool_checkout``，「观测绝不得影响
    借还」）；此处给缺保护的 abort 族在**函数内**补上，保护不再依赖各调用点自觉。
    """
    try:
        emit()
    except Exception:  # noqa: BLE001 — 观测层故障降级为 debug 记录
        logger.debug("metrics_emit_failed", exc_info=True)


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
    _safe_emit(
        lambda: plan_run_abort_lock_seconds.labels(
            phase=(phase or "unknown")[:32]
        ).observe(value)
    )


# 值域白名单（#1927 的基数纪律）：label 值必须是有界集合，否则 Python client 的
# 子序列永不回收——观测面自己变成泄漏面。
# 值域与 `database.classify_pool_checkout_failure` 一一对应；#2959 加了 slots_exhausted
# （PG 拒新建连接）。**两边必须同步**：这里漏一个值，那条失败就会被静默折叠回
  # "error"，而告警按 kind 分派——折叠等于把刚建立的可分辨性又抹掉。
_DB_POOL_CHECKOUT_FAILURE_KINDS = ("timeout", "slots_exhausted", "error")
_ABORT_FANOUT_SCOPES = ("run", "host")


def record_db_pool_checkout(engine_label: str, seconds: float):
    """#703：借到一条池连接用了多久（排队 + 建连 + pre-ping）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    db_pool_checkout_seconds.labels(engine=(engine_label or "unknown")[:32]).observe(value)


def record_db_pool_checkout_failure(engine_label: str, kind: str):
    """#703/#2959：借不到连接。`timeout`=池内排队超时，`slots_exhausted`=PG 拒绝新建
    连接（槽位耗尽），其余记 `error`。未列入白名单的值一律归 `error`（基数纪律）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    normalized = kind if kind in _DB_POOL_CHECKOUT_FAILURE_KINDS else "error"
    db_pool_checkout_failures_total.labels(
        engine=(engine_label or "unknown")[:32], kind=normalized
    ).inc()


def record_plan_run_abort_fanout(scope: str, jobs: int):
    """#703/#1880：一次 abort 调用实际牵动的 job 数（按 run / host 作用域）。"""
    if not PROMETHEUS_AVAILABLE:
        return
    try:
        value = int(jobs)
    except (TypeError, ValueError):
        return
    if value < 0:
        return
    normalized = scope if scope in _ABORT_FANOUT_SCOPES else "unknown"
    _safe_emit(
        lambda: plan_run_abort_fanout_jobs.labels(scope=normalized).observe(value)
    )


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
        _note_host_child(host_operation_slots_held, hid, ())
        _note_host_child(host_operation_slots_max, hid, ())
        _note_host_child(host_operation_waiters, hid, ())
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

    ``version`` / ``commit`` 的默认值刻意是 ``unknown``：真值来自部署树根的
    ``release-manifest.json``（站点安装形态），**没有清单的 checkout 形态**则由
    ``resolve_build_info()`` 报 ``checkout``/``checkout-dirty`` + ``git rev-parse HEAD``
    （#2572——本机生产控制面就是这种形态）；两者都取不到才显式回落 ``unknown``。
    **任何一档都不回落具体版本号**，否则又会造出「看起来有答案」的假信息。

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
