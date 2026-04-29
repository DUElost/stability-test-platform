# ADR-0019: Android Device Lease 与容量调度模型

- 状态：Proposed
- 优先级：P0
- 目标里程碑：M3
- 日期：2026-04-28
- 决策者：平台研发组
- 标签：调度, 设备锁, 容量, 租约, 恢复

## 背景

当前平台的并发控制存在四个结构性不足：

### 1. 调度粒度粗糙

Agent 用 `MAX_CONCURRENT_TASKS`（默认 2）控制并发，然后调用 `POST /api/v1/agent/jobs/claim` 拉取任务。这个模型假设"Agent 能跑几个任务"等同于"有几个健康空闲设备"，但实际情况是：

- 设备可能 ADB 断连、电量过低、温度过高，此时不应接收新任务
- Host CPU/内存/磁盘满载时不应继续派发
- `MAX_CONCURRENT_TASKS` 是静态配置，不反映动态容量

### 2. 设备锁缺少 fencing 保护

`backend/services/device_lock.py` 的 `acquire_lock` 用 `lock_run_id` + `lock_expires_at` 做 CAS，但没有 fencing token。这意味着：

- Agent 崩溃后旧进程残留的上报仍可能被接受（只要 `lock_run_id` 匹配）
- 网络分区恢复后过期的续租请求无法被识别和拒绝
- 无法区分"同一 Host 上的新旧 Agent 进程"

### 3. 临时脚本绕过设备锁

`ScriptBatch` + `ScriptExecution` 走独立链路（`backend/agent/script_batch_runner.py` + `backend/api/routes/agent_script_api.py`），有自己的 claim 端点和执行器，不与 `device_lock` 交互。正式任务和临时脚本可能抢同一台设备。

### 4. 恢复粒度不足

Agent 重启后没有对账机制——它不知道控制面是否还承认本地未完成的 job。当前靠 `session_watchdog.py` 在服务端做超时回收，但 Agent 侧只能丢弃所有本地状态重新开始。outbox 机制（`backend/agent/registry/local_db.py` 的 `log_signal_outbox`）只覆盖 log_signal 和 complete 上报，不覆盖运行时状态恢复。

## 决策

核心原则：**Android device 是一等调度资源。所有任务运行必须持有 active device lease。Agent 通过 capacity / available_slots 接收背压。控制面通过 Reconciler 对账恢复。**

### 1. Device Lease 模型

新增 `device_leases` 表，作为设备占用的权威记录：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer PK | |
| `device_id` | integer FK | 关联 device.id |
| `job_id` | integer FK | 关联 job_instance.id。`JOB`/`SCRIPT` 必填，`MAINTENANCE` 可为空（此时需填 `reason` VARCHAR(256) NOT NULL 和 `holder` VARCHAR(128)，说明占用原因和操作者） |
| `host_id` | string FK | 关联 host.id |
| `lease_type` | enum | `JOB` / `SCRIPT` / `MAINTENANCE` |
| `status` | enum | `ACTIVE` / `RELEASED` / `EXPIRED` |
| `fencing_token` | string | `"{device_id}:{lease_generation}"` |
| `lease_generation` | integer | per-device 单调递增 |
| `agent_instance_id` | string | Agent 进程 UUID |
| `acquired_at` | timestamptz | |
| `renewed_at` | timestamptz | |
| `expires_at` | timestamptz | |
| `released_at` | timestamptz | nullable |

约束：

- **Partial unique index**：`UNIQUE (device_id) WHERE status = 'ACTIVE'` — 同一 device 同一时间只能有一个 ACTIVE lease
- **Foreign keys**：`device_id → device.id`, `job_id → job_instance.id`, `host_id → host.id`

### 2. Fencing Token

fencing_token 使用 **Postgres 行级原子递增**生成，计数器放在 `device` 表（而非 `device_leases`，因为历史表可能没有行或多行）：

```sql
UPDATE device
SET lease_generation = lease_generation + 1
WHERE id = :device_id
RETURNING lease_generation;
```

Phase 1 migration 需新增 `device.lease_generation INTEGER NOT NULL DEFAULT 0`。`device_leases.lease_generation` 仅保存本次 lease 获取时拿到的 generation 快照，不原地递增。

**为什么不用 Redis INCR**：claim/lease 不是热路径（每秒 < 100 次），Postgres 强一致比 Redis 低延迟更重要。Redis 数据回退（AOF 截断、主从切换）会导致 fencing token 重复，旧 Agent 进程的上报可能被误认为合法。

控制面所有接受 Agent 上报的端点（renew、complete、upload log、upload trace）必须校验 `fencing_token == active_lease.fencing_token`。不匹配时返回 409 或记录 stale event，不推进状态机。

### 3. 容量模型

Agent 的可用容量不再仅看 `MAX_CONCURRENT_TASKS`，而是多维计算：

```
available_slots = min(
    online_healthy_devices,       # ADB 在线且健康检查通过
    host_config_limit,            # Host.max_concurrent_jobs（可配置上限，替代原 cpu_quota 的并发含义）
    cpu_health_limit,             # CPU 使用率 < 阈值
    mem_health_limit,             # 内存使用率 < 阈值
    disk_health_limit,            # 磁盘使用率 < 阈值
    adb_health_limit,             # ADB server 响应正常
) - running_jobs
```

Phase 1 在 `host` 表新增 `max_concurrent_jobs` 字段（integer, NOT NULL, DEFAULT 2），作为该 Host 的设备槽位上限。原 `cpu_quota` 字段不再用于并发控制，避免 CPU 配额与设备槽位语义混淆。

`available_slots` 是 Agent 侧的自检提示，也是控制面在 claim 端点入口的快速短路条件（`available_slots <= 0` 时直接返回空列表）。但 `available_slots` **不是硬上限**——并发 claim 请求可能同时看到可用槽位，最终实际 claim 数量以事务内 active lease / running job 计数为准：`INSERT INTO device_leases` 受 partial unique index 约束，同一 device 的第二个并发 INSERT 会失败，savepoint 回滚后该 job 回到 PENDING。

### 4. Claim 流程变更

当前 claim 流程（简化）：

```
SELECT PENDING jobs → for each job: savepoint(transition RUNNING + acquire_lock) → commit
```

变更后：

```
SELECT PENDING jobs
  WHERE device_id NOT IN (SELECT device_id FROM device_leases WHERE status = 'ACTIVE')
  ORDER BY id
  FOR UPDATE SKIP LOCKED
  LIMIT :capacity
→ for each job: savepoint(
    transition RUNNING
    + INSERT INTO device_leases (ACTIVE, fencing_token)
    + UPDATE device SET lock_run_id, lock_expires_at  -- 双写兼容
) → commit
```

关键变化：

1. **`FOR UPDATE SKIP LOCKED`**：两个并发 claim 请求不会互相阻塞，各自跳过对方已锁定的行，拿到不相交的 PENDING job 集合。配合 `ORDER BY id` 保证 FIFO 公平性，消除了当前 `LIMIT` 前置 + Python 循环过滤导致的饥饿问题。
2. **active lease 子查询过滤**：在 SQL 层排除已有 ACTIVE lease 的 device，避免拿到的 PENDING job 在 savepoint 中必然冲突回滚。这替代了当前 `acquire_lock` 的 CAS WHERE 条件。
3. **partial unique index 兜底**：即使子查询和 `SKIP LOCKED` 之间有竞态窗口，`INSERT INTO device_leases` 仍受 `UNIQUE (device_id) WHERE status = 'ACTIVE'` 约束保护——第二个并发 INSERT 会失败，savepoint 回滚后该 job 保持 PENDING，可被下次 claim 重试。

### 5. Reconciler

Reconciler 作为 APScheduler `IntervalTrigger` job 运行，分多个节奏：

| 检查项 | 间隔 | 动作 |
|--------|------|------|
| Host heartbeat 超时 | 10-15s | 标记 Host OFFLINE，暂停分配新任务 |
| Device lease 过期扫描 | 10-15s | lease 过期 → job UNKNOWN，冻结 fencing_token |
| UNKNOWN grace 超时 | 30s | grace 超时 → job FAILED/ABORTED，释放 lease |
| Device health 对账 | 30-60s | 设备离线 → 释放或冻结对应任务 |
| Running job stale 检查 | 30s | job 无心跳/无 step 更新 → 标记 STALE |

**两段式过期处理**（防止网络抖动误杀长稳任务）：

1. **lease 过期**（`expires_at < now`）：标记 job UNKNOWN，冻结旧 fencing_token，停止接受旧上报
2. **grace 超时**（UNKNOWN 持续 2-5min）：根据重试策略 requeue / FAILED / ABORTED，释放设备 lease

**Lease TTL 参数**：

| 参数 | 起步值 | 目标值 | 说明 |
|------|--------|--------|------|
| Agent renew interval | 30s | 30s | |
| Lease TTL | 600s | 300s | 起步与当前 `lock_expires_at` 对齐 |
| Reconciler lease 扫描 | 15s | 10s | |
| UNKNOWN grace | 5min | 2min | |

TTL 降为 300s 的前提：Agent LeaseRenewer 稳定运行至少一周，续租成功率 > 99.9%。

### 6. Agent 恢复接口

新增 `POST /api/v1/agent/recovery/sync`：

**请求**：
```json
{
  "host_id": "host-12",
  "agent_instance_id": "uuid4-generated-on-start",
  "boot_id": "linux-boot-id-from-proc-sys",
  "local_runs": [
    {
      "job_id": 1001,
      "device_id": 29,
      "fencing_token": "29:42",
      "pid": 12345,
      "state": "running"
    }
  ],
  "terminal_outbox": [1002, 1003]
}
```

**响应**：
```json
{
  "actions": [
    {"action": "RESUME", "job_id": 1001, "device_id": 29, "fencing_token": "29:42", "lease_expires_at": "..."},
    {"action": "CLEANUP", "job_id": 1004, "device_id": 31, "reason": "stale_token"},
    {"action": "UPLOAD_TERMINAL", "job_id": 1002}
  ]
}
```

**动作语义**：

| Action | 含义 | Agent 行为 |
|--------|------|-----------|
| `RESUME` | 控制面承认该 lease | 继续续租和执行 |
| `CLEANUP` | 本地进程过期或 token 不匹配 | 清理进程组和设备状态 |
| `UPLOAD_TERMINAL` | 本地已有终态但服务端缺失 | 补传 complete 上报 |
| `ABORT_LOCAL` | 服务端已不承认 | 立即停止进程，释放设备 |
| `NOOP` | 无需动作 | |

Agent 启动流程变为：`discover devices → recovery sync → claim new jobs`。只有 recovery sync 返回 RESUME 的 job 才能被恢复执行。

`agent_instance_id` 在 Agent 启动时用 `uuid.uuid4()` 生成，存储到内存和 SQLite。`boot_id` 从 `/proc/sys/kernel/random/boot_id` 读取，用于检测 Host 是否重启过。

### 7. 临时脚本统一

`ScriptBatch` + `ScriptExecution` 不再走独立链路：

- 临时脚本批次 → 转换为 Batch Job（一种 Job Template）
- 单设备脚本执行 → JobInstance（`lease_type: SCRIPT`）
- 脚本参数 → Job payload
- 设备占用 → 统一 device lease
- 日志/结果 → 统一 Job 日志与 StepTrace

迁移稳定后废弃并移除 `backend/api/routes/agent_script_api.py` 和 `backend/agent/script_batch_runner.py`。移除前保留为 deprecated stub，确保回滚路径。

### 8. 旧字段兼容

`device.lock_run_id` 和 `device.lock_expires_at` 不立即删除，按六阶段渐进迁移：

| 阶段 | 内容 | 验证标准 |
|------|------|----------|
| Phase 1 | 新增 `device_leases` 表（Alembic migration），不删除旧字段 | migration 可正向/回滚 |
| Phase 2 | claim job 时双写 `device_leases` + `device.lock_run_id/lock_expires_at` | 同一事务，两端数据一致 |
| Phase 3 | 续租时同时更新 `lease.expires_at` 和旧 `lock_expires_at` | extend_lock 同步更新两处 |
| Phase 4 | 读路径逐步改为优先读 `device_leases`，旧字段仅 fallback | 按 host 灰度，无回归 |
| Phase 5 | ScriptBatch 改用同一套 device lease | 旧脚本端点可废弃 |
| Phase 6 | 旧字段保留为 UI/兼容投影，稳定后决定是否删除 | |

## 备选方案与权衡

### 方案 A：维持当前模型 + 局部修补（未采纳）

- 优点：零迁移成本。
- 缺点：不解决 fencing、ScriptBatch 绕过锁、恢复粒度、容量模型四个结构性缺陷。修补会导致 `device_lock.py` 和 `session_watchdog.py` 进一步膨胀。

### 方案 B：Redis-based lease manager（未采纳）

- 优点：Redis 原子操作性能高，lease 续租延迟低。
- 缺点：Redis 数据回退风险（AOF 截断、主从切换），fencing token 可能重复；引入 Redis 作为 lease 强依赖增加运维复杂度。

### 方案 C：Postgres device_leases + fencing token（当前决策）

- 优点：强一致，可利用现有 PostgreSQL 事务保证；claim 不是热路径，性能足够。
- 缺点：比 Redis 方案略高的 DB 负载（但可接受）；需要 migration + 双写阶段。

## 不变量（护栏）

1. **ADR-0017 双通道原则**：HTTP 是权威写入路径，SocketIO 是展示路径。Lease 操作（acquire/renew/release）必须走 HTTP。
2. **ADR-0018 单进程约束**：Reconciler 作为 APScheduler 回调运行在 FastAPI 进程内，不引入额外独立进程。
3. **ADR-0003 状态机保留**：Job 状态转换路径不变（PENDING → RUNNING → COMPLETED/FAILED/ABORTED/UNKNOWN），本次只增补 fencing 校验和 lease 双写。
4. **ADR-0017 Outbox 幂等保留**：Agent outbox + drain 机制不变，409 冲突语义不变。
5. **Claim 原子性**：`transition RUNNING + INSERT lease` 必须在同一 savepoint 内完成，任一步失败全部回滚。

## 影响

### 正向影响

- 设备冲突消除：fencing token 精确拦截旧进程/网络分区后的过期上报
- 容量判断准确：available_slots 反映真实设备和 Host 状态，避免超载
- 恢复能力增强：Agent 重启后通过 recovery sync 对账，不再丢弃所有本地状态
- 临时脚本统一：ScriptBatch 纳入同一调度、锁、恢复机制，消除两条链路的一致性债务
- 观测更清晰：Host、Device、Job、Lease 四层状态可独立追踪

### 代价与约束

- 新增 `device_leases` 表 + Alembic migration
- 新增 `POST /api/v1/agent/recovery/sync` 端点
- Agent 侧新增 `LeaseRenewer`（替代当前 `LockRenewalManager`）、`CapacityReporter`
- 六阶段迁移期间需维护双写路径
- ScriptBatch 统一需前端协调（脚本执行页面改用 Job 模型）

## 落地顺序

| 阶段 | 范围 | 预计时间 |
|------|------|----------|
| Phase 1 | 资源模型：`device_leases` 表 + Host capacity 字段 + Agent 容量上报 | 2-3 天 |
| Phase 2 | Claim 改造：SQL 原子 claim + lease 双写 + fencing token 校验 | 2-3 天 |
| Phase 3 | Agent 侧：LeaseRenewer + CapacityReporter + recovery sync | 2-3 天 |
| Phase 4 | Reconciler：两段式 lease 过期 + UNKNOWN grace + stale job 检测 | 2-3 天 |
| Phase 5 | 临时脚本统一：ScriptBatch → JobInstance，移除独立链路 | 3-4 天 |
| Phase 6 | 验证与清理：3→10→44 host 分阶段压测，降 TTL 到 300s | 持续 |

## 关联 ADR

- **扩展 ADR-0003**：将 `lock_run_id`/`lock_expires_at` 设备锁模型升级为 `device_leases` + fencing token
- **扩展 ADR-0004**：心跳上报新增容量维度（available_slots、device health）
- **兼容 ADR-0017**：所有护栏条款不变
- **兼容 ADR-0018**：Reconciler 作为 APScheduler job 运行
- **推进 ADR-0011**：Lease、Host、Device 维度告警为可观测性落地提供新指标

## 关联实现/文档

### 当前（待变更）
- `backend/services/device_lock.py` — acquire/extend/release 将双写到 device_leases
- `backend/api/routes/agent_api.py` — claim 端点（`POST /jobs/claim`）重构
- `backend/tasks/session_watchdog.py` — 部分逻辑迁移到 Reconciler
- `backend/agent/main.py` — `MAX_CONCURRENT_TASKS` → available_slots
- `backend/agent/task_executor.py` — `LockRenewalManager` → `LeaseRenewer`
- `backend/models/host.py` — Device 模型旧字段保留为兼容投影
- `backend/api/routes/agent_script_api.py` — Phase 5 移除
- `backend/agent/script_batch_runner.py` — Phase 5 移除

### 新增
- `backend/models/device_lease.py` — DeviceLease ORM
- `backend/services/lease_manager.py` — Lease 管理服务（替代 device_lock 核心逻辑）
- `backend/scheduler/reconciler.py` — Reconciler APScheduler job
- `backend/api/routes/agent_recovery.py` — `POST /recovery/sync`
- `backend/agent/lease_renewer.py` — Agent 侧 LeaseRenewer
- `backend/agent/capacity_reporter.py` — Agent 侧容量上报
