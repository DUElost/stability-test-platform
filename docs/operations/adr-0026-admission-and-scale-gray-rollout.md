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
| 2026-07-22 | 控制面重启开准入 | `STP_PLAN_ADMISSION_QUEUE_ENABLED=1` 写入 `backend/.env` 并重启 uvicorn；`/health` 三者 true。证据：本表 + `/tmp/adr0026-validation-20260722.json` |
| 2026-07-22 | 库存 / 阶梯 | **ONLINE host=20**（device ONLINE≈41）；`achieved_tier=below_44_current_20`——**未达** ADR-0019/0026 的 44→60→100 |
| 2026-07-22 | API 冒烟（复测） | 7/7：idle PlanRun34 `QUEUED→RUNNING` **1.01s**；BUSY 竞争 PlanRun36 保持 `QUEUED`、未 FAILED；QUEUED abort 可终态；7 host 并发 PlanRun37–43 **5.29s** 全入 RUNNING |
| 2026-07-22 | 窗口指标 | queue-latency：本窗 count=10、sum≈17.4s（含并发票）；extend-batch `renewed=24`（成功率按 outcome 仅见 renewed）；O(1) aggregation path=counters ×9、sum≈0.4ms；单 host 12-job 跑窗内 **UNKNOWN=0**（误杀代理） |
| 2026-07-22 | 单 host 并行+串行 | host `172-21-9-131`（Agent `ops.max=5` matched `e12bd4a`）：PlanRun44/45 各 **12 device 同时 RUNNING** → **并行成立**。控制面采样 **未捕获** `EXECUTING_STEP`/`WAITING_EXECUTION_SLOT`（密采样 250ms 仍无）→ **permit 串行未在本窗用子状态直接证伪/证实**；心跳已暴露 `extra.operations.max=5`。证据：`/tmp/adr0026-dense-permit-20260722.json` |
| 2026-07-22 | Barrier 实机异常 | 12/12 job 进入 `WAITING_BARRIER` 后 **未推进 PATROL**（`plan_run_host.phase` 仍 null，观察 ≥45s）；属 INIT→PATROL barrier 接线实机缺陷，需另开修复。与「长跑并行」正交，但阻塞 patrol 阶段 permit 风暴观测 |
| 2026-07-22 | 多实例 | **仍暂缓**（单 uvicorn；Redis adapter / sid registry=false） |
| 2026-07-22 | 复测准入冒烟 | `/health` 三者 true；idle PlanRun `QUEUED→RUNNING` ≤5s；BUSY→`QUEUED`+`DEVICE_BUSY` 未 FAILED；QUEUED abort → **FAILED**（合同如此，非 CANCELLED）；7 host 并发入场全过。证据：`/tmp/adr0026-validation-20260722c.json` |
| 2026-07-22 | 阶梯 | ONLINE host **仍=20** → `below_44_current_20`；无法宣称 44/60/100 |
| 2026-07-22 | 窗口指标（复测） | queue-latency / extend-batch / aggregation 有增量；dense SQL 窗 **UNKNOWN=0** |
| 2026-07-22 | 单 host 并行（SQL） | PlanRun79：10 device → `RUNNING` 并发 → **并行 PASS**。证据：`/tmp/adr0026-metrics-sql-probe.json` |
| 2026-07-22 | 单 host 串行 permit | prometheus `slots_held{172-21-9-131}` 全程 peak=0（INIT 窗 ~15s）；`ops.max=5` 有配置。**串行竞争未直接证实**（步骤过快或 held 未在心跳节拍内暴露） |
| 2026-07-22 | Barrier 复现 | PlanRun79：10/10 `WAITING_BARRIER` 卡住；进程内 10 线程 barrier 单测 PASS → 疑 Agent 侧 peer 计数/多实例视图，非纯算法错误 |
| 2026-07-22 | 可观测性缺口 | `JobInstanceOut` 原先**不含** `execution_state`，API 采样天然失明；已补字段（需重启 uvicorn）。此前「API 看到 WAITING_BARRIER」实为 SQL/DB 路径 |
| 2026-07-22 | 晚间复测 round3 | `/health` 准入三者 true；idle PlanRun66 `QUEUED→RUNNING` **5.04s**；BUSY 竞争 PlanRun68 保持 `QUEUED`；7 host 并发 PlanRun69–75 全 `RUNNING`（**33.16s**，偏慢）；证据：`/tmp/adr0026-validation-20260722-round3.json` |
| 2026-07-22 | 晚间窗口指标 | queue-latency count=10 sum≈178.8s avg≈17.9s（含多 host 并发）；extend-batch renewed=22 **成功率 1.0**；aggregation counters ×9 avg≈0.04ms；unknown=0 |
| 2026-07-22 | 单 host 并行（晚间） | PlanRun80：host `172-21-9-131` **12/12 RUNNING** → **并行 PASS**。证据：`/tmp/adr0026-dense-permit-20260722-r3c.json` |
| 2026-07-22 | 单 host 串行（晚间） | Prometheus `slots_held` + host `extra.operations.held/waiting` 全程 peak=0；`ops.max=5` 仍在心跳。**串行竞争仍未直接证实**。证据：`/tmp/adr0026-dense-permit-20260722-r3d.json` |
| 2026-07-22 | 阶梯 / 多实例 | ONLINE host **仍=20** → `below_44_current_20`；多实例 **仍暂缓** |
| 2026-07-22 | 设计确认结论 | **并行（长跑）已实机成立**；**串行（permit cap）仅有配置/代码证据，缺 live held>0**；**INIT→PATROL barrier 实机卡住（待修）**——阻塞 patrol 阶段 permit 风暴观测 |
| 2026-07-22 | Barrier + permit 收口复测 | 修复后 PlanRun92（host `172-21-9-131`，14 devices）：`QUEUED→RUNNING=4.09s`；14/14 完成 cycle 1 并进入 `PATROL`，PRH `phase=PATROL`；`peak_held=5`、`peak_waiting=9`、`acquired_total=42`、`queued_total=32`；UNKNOWN=0。证据：`/tmp/adr0026-barrier-permit-closeout-20260722.json` |
| 2026-07-22 | 收口清理 | PlanRun92 为有意中止，最终 run status=`FAILED`（现有 abort 合同）；14/14 jobs=`ABORTED`、`terminal_job_count=14`、UNKNOWN=0，6.04s 内完成清理。该状态不计作业务失败样本。 |
| 2026-07-22 | 当前验收判定 | **Staging 准入冒烟通过；单 host 长跑并行通过；瞬时脚本/ADB 串行 cap=5 已有 live 竞争证据；Barrier 已通过。44→60→100 仍因库存阻塞（20 ONLINE host / 41 ONLINE device）；多实例仍因单 uvicorn + adapter/registry=false 暂缓。** |

## 5. 2026-07-22 收口判定与后续门槛

### 已关闭

- **INIT→PATROL Barrier**：Agent 在 barrier 等待时报告 `BARRIER_WAIT`，最后一个到达者推进 `PATROL`；Coordinator heartbeat 传递并持久化 `phase`。Patrol 步骤统一经过 host-global `OperationScheduler`，不再绕过 permit；进入 patrol sleep 时报告 `PATROL_SLEEP`。
- **瞬时操作串行语义**：PlanRun92 的 14 个设备共享 `STP_MAX_CONCURRENT_OPERATIONS=5`，实测高水位 `5` 且存在等待者（`9`），因此 cap=5 的 live 竞争成立；长跑设备并行仍为 14/14 RUNNING。

### 仍阻塞 / 不得宣称通过

| 门槛 | 当前证据 | 处理条件 |
|------|----------|----------|
| 44→60→100 host 阶梯 | 当前 API 复核为 20/20 host ONLINE、41 台 device ONLINE，`below_44_current_20` | 补足至少 44 个 ONLINE host 后，按每档完整长跑重新记录四项指标 |
| 多实例 SocketIO | 当前 `/health`：单 uvicorn；`socketio_redis_adapter=false`、`agent_sid_registry=false` | 准备 ≥2 控制面进程、Redis 可达、leader-election/LB 窗口后，再滚动开启并验证跨实例 RPC/断线重连 |

### 本次代码与回归证据

- Agent tests：`744 passed`；`compileall backend`：通过。
- Frontend：`npx tsc --noEmit` 与 `npm run build`：通过。
- 控制面指标用例（隔离 Docker PostgreSQL）：`5 passed`（仅 1 个现有 Starlette/httpx deprecation warning）。
- 现场证据：`/tmp/adr0026-barrier-permit-closeout-20260722.json`；准入/指标历史证据仍见本表前述路径。
