# ADR-0027: 控制面水平扩展（Leader Election + 多实例）

- 状态：Accepted（P3-1 / P3-2 / P3-3 代码已落地；生产多实例仍为 **opt-in**，见 ADR-0025 D1）
- 优先级：P2
- 目标里程碑：M6
- 日期：2026-07-20
- 决策者：平台研发组 / 架构组
- 标签：水平扩展, leader election, APScheduler, SocketIO, 单进程约束

## 背景

ADR-0002 / ADR-0018 强制控制面**单进程**：APScheduler、SAQ in-process worker、SocketIO 同驻 FastAPI。ADR-0025 D1 将「水平扩展类改动」推迟，重启条件为设备池 >80、需要零停机滚动、或多控制平面。

ADR-0026 将 P3 标为远期方向：

1. 准入 pump 的 leader election
2. SocketIO Redis adapter / Centrifugo
3. 控制面多实例（解除全部后台 job 的单实例假设）

本 ADR 承接该方向：先落地最小安全原语，再正式化多实例守卫；**默认仍单实例行为零变化**。

## 决策

### P3-1（已落地）：Scheduler singleton job 的 Postgres advisory-lock leader election

- 模块：`backend/core/leader_election.py`
- 接线：`admission_pump.pump_admission_tick`、`counter_reconciler.reconcile_plan_run_counters_once`（函数内）
- 机制：`pg_try_advisory_lock(stable_key(job_name))`，锁仅在 tick 期间持有
- 开关：`STP_SCHEDULER_LEADER_ELECTION`（默认 `1`）
  - 单实例：永远抢到锁，行为与改造前一致
  - 多实例误部署：至多一个进程跑 singleton tick
  - `0`：关闭选举（调试/应急）
  - SQLite / `TESTING=1`：恒为 leader（本地与单测）

### P3-2（已落地）：SocketIO Redis adapter

- 模块：`backend/realtime/socketio_redis.py` → `create_sio_server()` 可选挂载 `AsyncRedisManager`
- 开关：`STP_SOCKETIO_REDIS_ADAPTER`（默认 `0`，**opt-in**）
  - `0` / 未设：进程内 manager，单实例零 Redis pub/sub 开销
  - `1`：用 `REDIS_URL` + channel `STP_SOCKETIO_REDIS_CHANNEL`（默认 `stp-socketio`）
  - `TESTING=1`：强制关闭（单测不连 Redis）
- `/health` 暴露 `socketio_redis_adapter` 布尔字段
- **已覆盖**：dashboard room 广播、`emit_agent_control`（`room=agent:{host_id}`）跨进程 fan-out

### P3-3（已落地）：控制面多实例正式化守卫

#### 全量 APScheduler singleton leadership

- `backend/scheduler/app_scheduler.py`：`_instrumented(..., singleton=True)` 对下列 job 包一层 `hold_scheduler_leadership`：
  - recycler / session_watchdog / device_lease_reconciler / cron_check / retention_cleanup
  - precheck_reaper / plan_chain_reconciler / revoked_token_cleanup / auto_archive_sweep
- **不**二次包裹 `admission_pump` / `counter_reconcile`（它们已有函数内 leadership；不同 Session 上嵌套同名 advisory lock 会导致 tick 误跳过）
- **不**包裹 `saq_queue_depth_poll`（每实例采样本进程可见的队列深度，可并行）

#### 共享 Agent sid 注册 + RPC 去 sticky

- 模块：`backend/realtime/agent_sid_registry.py`
- 开关：`STP_AGENT_SID_REGISTRY`（默认跟随 Redis adapter；可显式 `0`/`1`）
- connect 时 Redis `SET stp:agent:owner:{host_id}`（TTL 默认 120s）；disconnect 时 CAS delete
- `call_agent_rpc`：本地 sid 优先；否则在 adapter 开启时走 `room=agent:{host_id}`（Redis manager 投递到持有连接的实例）；registry 开启时先查 owner，无登记则立即 `AgentNotConnectedError`（避免空 room 挂满 timeout）
- `/health` 暴露 `agent_sid_registry`

#### 与 ADR-0018 不变量 4

- **修订**：多实例部署在同时满足本 ADR 守卫时被允许（见 ADR-0018 修订记录）。
- **默认**：仍推荐单进程；未开 adapter / 未开 leader election 时行为与历史一致。
- SAQ in-process worker 仍可每实例各跑一个（共享 Redis 队列，由 SAQ 本身去重消费）；`STP_ENABLE_INPROCESS_SAQ=0` + 外部 worker 仍是可选拓扑。

## 生产多实例检查清单（opt-in）

1. `STP_SCHEDULER_LEADER_ELECTION=1`（默认）
2. `STP_SOCKETIO_REDIS_ADAPTER=1`
3. `STP_AGENT_SID_REGISTRY` 保持默认（跟随 adapter）或显式 `1`
4. Postgres + Redis 可达；LB 可不 sticky（RPC 走 room）
5. 仍以 ADR-0025 D1 重启条件为准，勿过早扩容

## 与 ADR-0025 D1 的关系

- **不推翻**「设备池未达阈值前不强制多实例」的产品判断。
- **补齐**误扩容时的安全网：全部 singleton APScheduler job 至多一跑；Redis adapter 后 dashboard / agent room 不分裂；sid registry + room RPC 去掉 sticky 依赖。

## 后果

- 正向：P3 三段可合并；单实例默认零行为变化；正式多实例路径可文档化。
- 负向：advisory lock 依赖 Postgres；Redis adapter / registry 增加 pub/sub 与 key 流量；跨实例 RPC 依赖 python-socketio Redis manager 的 room ack 路径。
- 回滚：`STP_SCHEDULER_LEADER_ELECTION=0` / `STP_SOCKETIO_REDIS_ADAPTER=0` / `STP_AGENT_SID_REGISTRY=0` 或 revert 对应接线。

## 关联

- 扩展 [ADR-0026](./ADR-0026-plan-execution-scaling.md) P3
- 修订 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md) 不变量 4（条件放宽）
- 对齐 [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) D1 重启条件
- 迁移纪律 [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md)（本步无 schema）

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-20 | 初稿 Proposed；P3-1 advisory-lock leader election 落地 |
| 2026-07-20 | P3-2：`AsyncRedisManager` opt-in（`STP_SOCKETIO_REDIS_ADAPTER`）；文档诚实边界（RPC sticky） |
| 2026-07-20 | P3-3：全量 singleton schedule leadership + Agent sid registry + room RPC；状态 → Accepted；解除 sticky 依赖 |
