"""调度域 Settings（ADR-0042 P1 试点）。

覆盖「调度节奏与回收」三个文件的环境旋钮：`backend/scheduler/app_scheduler.py`、
`recycler.py`、`cron_scheduler.py`（对账/重试批处理域，即 counter/signal_link/
plan_chain/precheck 四个 reconciler，留在 P2 迁移）。

口径：

- 字段名 = 既有 env 名的小写形式（`RUN_RECYCLE_INTERVAL_SECONDS` →
  `run_recycle_interval_seconds`），不改名、不加前缀；
- 默认值与迁移前 `int(os.getenv(..., "<默认>"))` 逐一对齐（含 `str(24 * 3600)` 类）；
- 类型：原实现用 `float(...)` 的（`CRON_POLL_INTERVAL`、`SCHEDULE_DEDUP_WINDOW_SECONDS`）
  保持 float，其余 int；
- 惰性：`get_scheduler_settings()` 逐次取值（lru_cache），`reset_scheduler_settings_cache()`
  供测试与将来热更能力使用（控制面当前不热更，ADR-0042 D4）。
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from backend.core.settings.base import DomainSettings


class SchedulerSettings(DomainSettings):
    """调度节奏与回收旋钮（env 名 = 字段名大写）。"""

    # ── app_scheduler：作业注册周期（秒）──
    run_recycle_interval_seconds: int = 30
    session_watchdog_interval_seconds: int = 15
    reconciler_interval_seconds: int = 15
    # #2531：租约回收器 Phase 2（UNKNOWN→释放租约+FAILED）与 stale 分支的**单轮排空上限**。
    # 修前这两个分支「处理一个候选就 break」，速率被钉在 1 台 / `reconciler_interval_seconds`
    # （dev 实测 12s/台 ⇒ 4 台/分钟；按 `agent_api` 的 60 host × ~17 device ≈ 1000 台容量口径
    # 外推，全量解锁 ≈3.3 小时，期间设备一直 DEVICE_BUSY）。现在一轮最多排空这么多候选，
    # 但**保持一候选一事务**（#1172 的「不跨候选混交父终态化」不变量不动）。
    # 下界 1（同 #2278 口径）：0 会让解锁静默永久停摆，且没有任何读数表明「无事可做」——
    # 想变慢请调小它但不得为 0。上界 1000：单轮 ∝ 批大小地拉长持锁与墙钟窗口（每候选
    # 含一次终态化提交 + 链式派发），超过全 fleet 规模没有意义，只会掩盖上一轮没跑完。
    reconciler_drain_batch: int = Field(default=20, ge=1, le=1000)
    # #2554：排空未完时**在同一次持锁内自续一轮**的墙钟预算（秒）。`reconciler_drain_batch`
    # 是**事务边界的保护**（限制单轮持锁窗口），不是速率旋钮——把它当旋钮调大只会让单轮
    # 持锁与 `_reconcile_lock` 被占用的时长一起变长。#2548 的容量探针实测每台排空 ≈8–9ms
    # 且线性，于是 1000 台按默认 cap=20 需要 50 个 tick × 15s ≈ 12.5 分钟，**其中真正
    # 干活的时间合计只有 ≈9 秒**：剩下全是躺在 IntervalTrigger 上等下一拍。本字段把那
    # 9 秒的活摊进一次持锁里跑完，同时保留「等 tick」的兜底节奏。
    # 默认 5s = 默认周期 15s 的 1/3 占空比：给同一进程里的其它 singleton job 留出余量，
    # 且远小于周期，不会触发 `reconciler_skip_previous_still_running`。
    # **0 = 关闭自续**（回到 #2548 的形状：一轮最多 cap 台，其余等下一拍）——这是
    # 显式的止血开关，不是无意义取值，所以 `ge=0`（区别于 `reconciler_drain_batch`
    # 不能为 0：那会让解锁彻底停摆）。
    reconciler_drain_max_seconds: float = Field(default=5.0, ge=0.0)
    cron_poll_interval: float = 30
    retention_cleanup_interval_seconds: int = 3600
    queue_depth_poll_interval_seconds: int = 15
    precheck_reaper_interval_seconds: int = 45
    chain_reconciler_interval_seconds: int = 60
    # #2755：链触发的最小稳定窗——父 run 终态后至少隔这么多秒才触发下一段
    # （设备从 monkey 浸泡/teardown 收敛需要时间；r431 实测 2s 间隔 init
    # 失败 40.6% vs 2.7h 间隔 4.6%）。窗锚定 ended_at，即时路径与 reconciler
    # 补偿路径都过同一道窗，故实际触发时刻 ≈ settle + (0~60s)。0 = 关闭（回退旧行为）。
    chain_trigger_settle_seconds: int = 180
    # 一天扫一次 expired jti 即可（refresh 黑名单只随主动登出增长；见原注释）
    revoked_token_cleanup_interval_seconds: int = 24 * 3600
    auto_archive_poll_interval_seconds: int = 120
    stp_admission_pump_interval_seconds: int = 5
    stp_counter_reconcile_interval_seconds: int = 300
    stp_signal_link_reconcile_interval_seconds: int = 300
    # #2958 第五道闸：host 脚本在位矩阵的 sweep 节奏（cron 五段，**本机时区**——
    # 与 stp-script-guard.timer 的 OnCalendar 同口径）。默认每天 09:30 一次，与既有
    # 每日治理窗对齐、避开链派发窗；账本是「存量可见性」而非实时告警，一天一跑足够
    # （单轮成本：每台一次 verify_scripts RPC，10s 超时内）。空串 = 显式停用（不注册，
    # 监控面读作「该作业不存在」）。
    script_presence_sweep_cron: str = "30 9 * * *"
    # 历史可达窗口（天）：决定「该 host 预期会跑到哪些版本」的历史面。
    script_presence_history_days: int = Field(default=30, ge=1, le=365)
    # #2983：控制面 host 健康探针周期（秒）。默认 600=10min；0 = 不注册该作业。
    host_health_probe_interval_seconds: int = Field(default=600, ge=0)
    host_health_probe_concurrency: int = Field(default=4, ge=1, le=32)
    host_health_probe_timeout_seconds: int = Field(default=10, ge=1, le=60)
    host_health_probe_strike_need: int = Field(default=2, ge=1, le=10)

    # ── recycler：批量、保留与宽限 ──
    recycler_batch_size: int = 200
    artifact_retention_days: int = 30
    patrol_stall_batch_limit: int = 100
    # #3061：step_trace 静默回收每轮条数（Pass #2c；0 = 与 patrol 同口径停用该 pass）
    step_trace_stall_batch_limit: int = Field(default=100, ge=0)
    coordinator_heartbeat_timeout_seconds: int = 300
    post_completion_grace_seconds: int = 120
    post_completion_max_defer_seconds: int = 6 * 3600

    # ── 对账/重试批处理（P2 迁移：counter / signal_link / plan_chain / precheck）──
    stp_counter_reconcile_lookback_hours: int = 48
    stp_counter_reconcile_batch: int = 200
    stp_signal_link_reconcile_batch: int = 200
    chain_reconcile_batch_size: int = 100
    max_precheck_reenqueue_attempts: int = 1
    max_admission_requeue_attempts: int = 3
    admission_requeue_backoff_seconds: int = 60

    # ── cron_scheduler：计划保留与触发去重 ──
    plan_run_retention_days: int = 3
    # #2105：单 tick 处理的 PlanRun 上限（`_retention_candidate_ids(limit=…)`）。
    # 保留清理事务的**持锁窗口 ∝ 批大小**——NFS 目录回收与行删除都在同一事务内
    # （#1521/#1698「先文件后行」），窗口用 `stability_retention_txn_seconds` 观测
    # （#2104）。窗口过长的杠杆是调小它；**不要**改成把 purge 挪出事务——那会造成
    # 「文件已删、行仍在」的不可自愈不一致（详见共享行加锁表 Revisit）。
    # #2278：下界 1。批大小可以为 1（窗口最短），但**不能为 0**——0 会让
    # `_retention_candidate_ids` 的 `while len(selected_ids) < limit` 一次都不执行，
    # 保留清理静默永久停摆，而 `stability_retention_candidate_runs` 如实显示 0，
    # 监控上读起来像「无积压」。想「少删」请调大 `PLAN_RUN_RETENTION_DAYS`；
    # 越界 env 在取值时即 ValidationError（作业显式失败并计入 apscheduler 错误
    # 指标），不把「配错」伪装成「没事干」。
    plan_run_retention_batch_size: int = Field(default=100, ge=1)
    schedule_dedup_window_seconds: float = 60

    # ── audit_log_cleanup：分层保留期（#2741 / ADR-0049，owner 裁决 2026-09-19）──
    # security 180d（安全事件链，对齐 ADR-0020 六个月先例）/ business 90d（默认桶，
    # 含 terminal_payload_conflict 爆发行——裁决明示不例外）/ session 30d（例行
    # 会话心跳）。`*_DAYS=0` 与 PlanRun 家族同义（cutoff=now，该层全量到期）。
    audit_log_session_retention_days: int = Field(default=30, ge=0)
    audit_log_business_retention_days: int = Field(default=90, ge=0)
    audit_log_security_retention_days: int = Field(default=180, ge=0)
    # 单 tick 每层处理上限（工作量可预期的杠杆；无行锁窗口顾虑，见模块 docstring）。
    audit_log_retention_batch_size: int = Field(default=5000, ge=1)
    # sweep 周期；0 = 显式停用（app_scheduler 不注册该作业——事故取证期冻结裁剪）。
    audit_log_retention_interval_seconds: int = Field(default=3600, ge=0)


@lru_cache(maxsize=1)
def get_scheduler_settings() -> SchedulerSettings:
    """取调度域 Settings（ADR-0042：惰性 + 缓存；不读 `.env` 文件）。"""
    return SchedulerSettings()


def reset_scheduler_settings_cache() -> None:
    """清缓存——测试改 env 后调用；将来若控制面支持热更重读亦走这里。"""
    get_scheduler_settings.cache_clear()
