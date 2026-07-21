# ADR-0026 / 0027 灰度 Runbook（准入队列 + 多实例）

> **目的**：把已合入 `main` 的代码能力按可控阶梯打开，而不是一次性改默认值。  
> **默认仍关**：`STP_PLAN_ADMISSION_QUEUE_ENABLED=0`、`STP_SOCKETIO_REDIS_ADAPTER=0`。  
> **配套**：[`ADR-0026`](../adr/ADR-0026-plan-execution-scaling.md) 待定清单；[`ADR-0027`](../adr/ADR-0027-control-plane-horizontal-scaling.md)。

---

## 0. 观测入口

```bash
curl -sS "$CONTROL_BASE_URL/health" | jq .data
```

关注字段：

| 字段 | 含义 |
|------|------|
| `admission_queue_flag` | env `STP_PLAN_ADMISSION_QUEUE_ENABLED=1` |
| `admission_queue_pump_ready` | APScheduler pump 已 `mark_queue_pump_ready` |
| `admission_queue_enabled` | **两者同时为真** 才真正走 QUEUED 路径 |
| `socketio_redis_adapter` | 多实例 room fan-out |
| `agent_sid_registry` | Agent owner 跨实例登记（默认同 adapter） |

Prometheus（已有）：queue-latency、extend-batch 成功率、aggregation、host-slots。

---

## 1. 准入队列灰度（单控制面实例即可）

### 1.1 前置

- [ ] Agent 已含 Step 5b（Coordinator 心跳 + OperationScheduler）
- [ ] barrier 接线已在 `main`（`STP_PHASE_BARRIER_ENABLED` 默认可保持开）
- [ ] `/health` 显示 `admission_queue_pump_ready=true`（进程已起 pump）
- [ ] 准备回滚：把 env 改回 `0` 并重启控制面（存量 QUEUED 仍会被 drain-only 消化）

### 1.2 开启

```bash
# 控制面 .env
STP_PLAN_ADMISSION_QUEUE_ENABLED=1
# 建议保持 v1 默认（见 adr0026_params）
# STP_ADMISSION_PUMP_INTERVAL_SECONDS=5
# STP_ADMISSION_PUMP_BATCH=5
# STP_ADMISSION_RETRY_BACKOFF_SECONDS=30
```

重启后端后确认：

```bash
curl -sS "$CONTROL_BASE_URL/health" | jq '{
  flag: .data.admission_queue_flag,
  pump: .data.admission_queue_pump_ready,
  enabled: .data.admission_queue_enabled
}'
# 期望三者均为 true
```

### 1.3 验收（每档）

| 检查 | 通过标准 |
|------|----------|
| 空闲资源「点了就跑」 | QUEUED→RUNNING 延迟 ≲ pump interval（默认 5s） |
| 设备忙 | 竞争失败回队，不把 PlanRun 标 FAILED（相对 legacy） |
| 取消 | 取消中的 QUEUED/PRECHECK 可终态，无永久搁置 |
| 指标 | queue-latency / admission 相关无异常尖刺 |

### 1.4 Host 阶梯（与 ADR-0026 一致）

沿用 **44 → 60 → 100 host**：每档至少完整跑通若干长跑 PlanRun，再进下一档。每档记录：续租成功率、准入延迟、聚合耗时、误杀率（recycler UNKNOWN/FAILED）。

### 1.5 回滚

```bash
STP_PLAN_ADMISSION_QUEUE_ENABLED=0
# 重启控制面
```

新派发回到 legacy「点了就跑」；已在 QUEUED 的由 pump drain-only 消化，不会静默搁置。

---

## 2. 多实例灰度（ADR-0027，需 ≥2 控制面进程）

### 2.1 前置

- [ ] Redis 可达（与 SAQ 同 `REDIS_URL`）
- [ ] `STP_SCHEDULER_LEADER_ELECTION=1`（默认开；确认只有一个 leader 跑 singleton job）
- [ ] LB 可先保留 sticky，开 adapter 后再验证无 sticky 也能 RPC

### 2.2 开启

```bash
STP_SOCKETIO_REDIS_ADAPTER=1
# STP_AGENT_SID_REGISTRY=   # 空=跟随 adapter；显式 1 强制开
```

滚动重启全部控制面实例后：

```bash
curl -sS "$CONTROL_BASE_URL/health" | jq '{
  adapter: .data.socketio_redis_adapter,
  registry: .data.agent_sid_registry
}'
```

### 2.3 验收

| 检查 | 通过标准 |
|------|----------|
| Leader | 仅一个实例的 admission pump / counter_reconcile / 其它 singleton 在跑 |
| Agent RPC | 去掉 sticky 后 claim/abort/control 仍可达正确 Agent |
| 断线 | Agent 重连后 sid registry 更新，旧 owner 不抢答 |

### 2.4 回滚

```bash
STP_SOCKETIO_REDIS_ADAPTER=0
STP_AGENT_SID_REGISTRY=0
# 重启；临时恢复 LB sticky
```

---

## 3. 不要在本 Runbook 里改的东西

- **不要**把 `STP_PLAN_ADMISSION_QUEUE_ENABLED` 默认改成 `1`（须压测签字后再改 `.env.example` 默认并写 ADR 修订记录）
- **不要**在未开 Redis adapter 时水平扩展 SocketIO
- **待定参数**（permit / aging / barrier 超时）改默认前重跑 `backend/core/adr0026_params.py` 不变量套件

---

## 4. 灰度实测记录（internal）

| 日期 | 项 | 结果 |
|------|----|------|
| 2026-07-21 | `/health` 开准入 | `admission_queue_flag/pump_ready/enabled` 三者 true |
| 2026-07-21 | API 冒烟（首次） | `POST /plans/5/run` → `QUEUED`，但 pump 报 `EnqueueSyncError: cannot synchronously enqueue plan_admission_task from the event loop`；根因：APScheduler 4 `AsyncScheduler` 默认 `async` executor 在事件循环上跑 sync tick |
| 2026-07-21 | 修复 | `create_scheduler` 默认 `threadpool`；async job 按 `_job_executor_for` 选 `async`/`threadpool` |
| 2026-07-21 | API 冒烟（修复后） | PlanRun `27`：`QUEUED`→`RUNNING` ≤5s，jobs=1；abort 可终态。证据：`/tmp/adr0026-admission-smoke.json` |
| 2026-07-21 | 参数套件 / 仿真 | `test_adr0026_params` 7 passed；permit 仿真 5–60 device / cap=3–8 见 `/tmp/adr0026-param-sim.json`；**保持 v1 默认**（permit=5 等），未改 `.env.example` |
| 2026-07-21 | 多实例决策 | **暂不开**：仅 1 个 uvicorn；Redis adapter / sid registry 均为 false；待 ≥2 控制面进程 + sticky 验证窗口后再开 `STP_SOCKETIO_REDIS_ADAPTER` |
| 2026-07-21 | API 冒烟（复测） | `/health` 三者 true；空闲 PlanRun `33`：`QUEUED`→`RUNNING` **1.02s**；BUSY 竞争 PlanRun `29` 观察窗内保持 `QUEUED`、未 FAILED；QUEUED abort 可终态；3 路并发 PlanRun `30–32` 均 ≤2s 入场。证据：`/tmp/adr0026-smoke-pressure-20260721.json`、`/tmp/adr0026-idle-recheck.json` |
| 2026-07-21 | 冒烟异常观测 | PlanRun `28` 首票入场 **34s**：`queue_blockers=admission_enqueue_failed`，日志 `SAQ not running — cannot enqueue plan_admission_task`，命中 `ADMISSION_RETRY_BACKOFF_SECONDS=30` 后成功；属 SAQ 瞬时未就绪 + 退避，非排队语义错误 |
| 2026-07-21 | 参数套件（复测） | `test_adr0026_params` 7 passed；仿真 max mean wait=12.3s ≪ coord timeout 300s；**仍保持 v1 默认**。证据：`/tmp/adr0026-param-sim-20260721.json` |
| 2026-07-21 | 小规模压测结论 | 当前实机 ≈14 ONLINE / 8 BUSY、单控制面；API 侧小并发准入通过。**不能替代** 44→60→100 host 阶梯；多实例仍暂缓 |
