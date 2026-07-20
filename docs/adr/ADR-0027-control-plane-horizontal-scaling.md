# ADR-0027: 控制面水平扩展（Leader Election + 多实例预备）

- 状态：Proposed（P3-1 代码已落地，完整多实例尚未启用）
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

本 ADR 承接该方向，**先落地最小安全原语**，不在本轮打开多实例生产开关。

## 决策

### P3-1（本轮）：Scheduler singleton job 的 Postgres advisory-lock leader election

- 模块：`backend/core/leader_election.py`
- 接线：`admission_pump.pump_admission_tick`、`counter_reconciler.reconcile_plan_run_counters_once`
- 机制：`pg_try_advisory_lock(stable_key(job_name))`，锁仅在 tick 期间持有
- 开关：`STP_SCHEDULER_LEADER_ELECTION`（默认 `1`）
  - 单实例：永远抢到锁，行为与改造前一致
  - 多实例误部署：至多一个进程跑 singleton tick
  - `0`：关闭选举（调试/应急）
  - SQLite / `TESTING=1`：恒为 leader（本地与单测）

**不做**：本轮不改 SocketIO adapter、不拆 APScheduler 外置、不强制多 worker 部署。

### P3-2（后续）：SocketIO Redis adapter

对齐 ADR-0018 预留与 ADR-0025 D1 重启条件；独立 PR，需压测 room 广播与 Agent namespace。

### P3-3（后续）：控制面多实例正式化

覆盖 recycler / session_watchdog / precheck reaper / cron 等全部 APScheduler job 的 leadership 或外置调度；解除 ADR-0018 不变量 4 需单独评审收口（本 ADR Accepted 后修订 ADR-0018）。

## 与 ADR-0025 D1 的关系

- **不推翻**「设备池未达阈值前不强制多实例」的产品判断。
- **补齐**误扩容时的安全网：即便运维先起了第二实例，admission pump / counter reconcile 也不会双跑。
- 正式多实例上线仍以 ADR-0025 D1 重启条件为准。

## 后果

- 正向：P3 有可合并的第一步；单实例零行为变化；多实例误部署风险下降。
- 负向：advisory lock 依赖 Postgres；SQLite 路径无跨进程互斥（可接受，非生产）。
- 回滚：`STP_SCHEDULER_LEADER_ELECTION=0` 或 revert `leader_election` 接线。

## 关联

- 扩展 [ADR-0026](./ADR-0026-plan-execution-scaling.md) P3
- 受约束于 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md) 不变量 4（本 ADR 未解除）
- 对齐 [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) D1 重启条件
- 迁移纪律 [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md)（本步无 schema）

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-20 | 初稿 Proposed；P3-1 advisory-lock leader election 落地 |
