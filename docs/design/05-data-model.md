# 数据模型

> **ORM**：`backend/models/`  
> **迁移**：`backend/alembic/versions/`  
> **约定**：**新表**表名一律单数（`device` 非 `devices`）。

### 复数历史例外（#889）

以下既有表为**复数命名的历史例外**，保留以避免纯重命名迁移（重命名生产表
是高风险动作，且无正确性收益——表名是存储层事实，显式性属于模型与文档层）：

| 表 | 说明 |
|----|------|
| `users` | 初版即复数命名（`b0f805bf6cee_add_users_table`） |
| `audit_logs` | 模型 docstring 明示：匹配既有生产表，避免危险重命名迁移 |
| `notification_channels` / `alert_rules` / `notification_logs` | 通知域历史表，同批保留 |
| `device_leases` | ADR-0019 Phase 1 历史命名 |
| `task_schedules` | 历史命名（Cron → `plan_id` 调度） |

**规则**：新建表必须单数；历史复数表仅原样保留——**禁止**仅为匹配文档措辞
而重命名生产表，任何重命名必须以显式迁移（含数据回填与下游 SQL/脚本核对）
单独立项评审。

---

## 1. 编排与执行

### Plan / PlanStep

| 表 | 说明 |
|----|------|
| `plan` | 编排定义：`name`、`patrol_interval_seconds`、`timeout_seconds`、`next_plan_id`、`watcher_policy` |
| `plan_step` | 步骤行：`script_name`、`script_version`、`stage`(init/patrol/teardown)、`sort_order`、`enabled` |

**设计理由（摘要）**：自 Workflow→TaskTemplate→Pipeline 五层嵌套收敛为 **Plan→Step**（ADR-0020）。一个 Plan = 一个完整专项；`next_plan_id` 替代 Plan 内多 Block；`patrol_interval_seconds IS NULL` 表示无 patrol 阶段；步骤参数来自 Script `default_params`，不在 Plan 行存 params。

**无** `plan.lifecycle` 列；派发时由 `plan_dispatcher_sync` 组装 `pipeline_def`。

**历史迁移对照**（已归档详述 [`archive/migrations/plan-block-step-migration.md`](../archive/migrations/plan-block-step-migration.md)）：

| 旧概念 | 新概念 |
|--------|--------|
| WorkflowDefinition | Plan |
| TaskTemplate + pipeline_def JSON | PlanStep 行 |
| WorkflowRun | PlanRun |
| setup/teardown JSONB | plan_step stage=init/teardown |
| patrol JSONB.interval | plan.patrol_interval_seconds |

### PlanRun

| 字段 | 说明 |
|------|------|
| `status` | RUNNING / SUCCESS / PARTIAL_SUCCESS / FAILED / QUEUED / PRECHECK（PARTIAL_SUCCESS=完成但有设备失败，黄；ADR-0048 v1.1 恢复产出，无阈值线、永不因设备失败判红）（QUEUED/PRECHECK 为准入队列状态，`STP_PLAN_ADMISSION_QUEUE_ENABLED` 默认开） |
| `plan_snapshot` | 派发时 JSON 快照 |
| `parent_plan_run_id` | Plan 链 |
| `run_type` | MANUAL / SCHEDULE / CHAIN |
| `run_context` | 含 `precheck` 等 |

**界面名对照**（[ADR-0059](../adr/ADR-0059-contract-and-sop-writing-conventions.md) D2-3：界面文案跨边界使用）：

| 推荐名 | 合法别称 | 不要当成 |
|---|---|---|
| PlanRun | 通知页事件名称中的「任务」（指 PlanRun；证据：`RUN_COMPLETED` / `RUN_FAILED` 在 `frontend/src/pages/notifications/NotificationsPage.tsx` 显示为「任务完成 / 任务失败」，事件由 `plan_run_finalization` 发出） | SAQ 任务、定时任务（`task_schedules`）、systemd timer、JobInstance |

### JobInstance

| 字段 | 说明 |
|------|------|
| `plan_run_id`, `plan_id` | NOT NULL |
| `device_id`, `host_id` | 扇出目标 |
| `status` | PENDING / RUNNING / COMPLETED / FAILED / ABORTED / **UNKNOWN**（围栏恢复态：心跳断/abort 未 ACK 时由 recycler 判入，持 ACTIVE 租约的 job 才可进入；合法转出仅 `UNKNOWN → RUNNING`（recovery 补报必先复活）与 `UNKNOWN → FAILED`（宽限期超时）——`state_machine.py` VALID_TRANSITIONS，#3001） |
| `pipeline_def` | 完整 lifecycle JSON |
| `disarm_results` | nullable JSONB；自 B6 激活起生效：本 job 建立过足迹的专项解除结果，六值与证据见执行协议 §9.1；init 检查通过独立入口上报，不混入本列 |
| `disarm_guard` | nullable JSONB；自 B6 激活起生效：归还尾段协议、T0、Q 余量、各步骤冻结预算/截止、阶段、序号与所有权锚点；不作为 Job 测试结论 |

上述 B6 列对应迁移 `c3d4e5f6a7b8`（disarm_results）与 `u8b6d3752a01`（disarm_guard）。
代码入口：`backend/models/job.py::JobInstance`；写入分别由
`backend/services/agent_completion.py::apply_disarm_results` 与
`backend/services/device_return.py::accept_job_disarm_evidence` 承担。
依据：[ADR-0061](../adr/ADR-0061-device-return-contract.md) D4、D3 第 8 条；
协议见 [`07-execution-protocol.md` §9](./07-execution-protocol.md#9-b6-执行与设备归还自-b6-激活起生效)。

### StepTrace

单 Job 步骤追踪：`job_id`、`step_id`、`stage`、`status`、`original_ts`。

### JobArtifact / JobLogSignal

| 表 | 说明 |
|----|------|
| `job_artifact` | 产物元数据（AEE、快照等）；`run_log_bundle` 方案 C 后 Agent 不再注册 |
| `job_log_signal` | Watcher 异常事件流（权威异常源） |

---

## 2. 基础设施

### Host / Device

| 表 | 说明 |
|----|------|
| `host` | 字符串 PK `host-101`；`status`、心跳、`extra` JSON |
| `device` | `serial` 唯一；`host_id`；`model` 与可空的 ADB `product`；电量/温度；`lease_generation` |

自 B6 激活起生效：`device.return_state` 为 nullable JSONB，按专项键存 `state`、`source`、`updated_at` 与相应 job/解除结果。
专项 state 为 `returned/debt/unverified`，source 为 `job/init_check/discharge/override/stock`。
设备聚合顺序为 debt＞unverified＞returned，缺已登记专项记录也为 unverified；空值在界面表示「未初始化」，不证明设备已归还。
代码入口：`backend/models/host.py::Device`；`backend/services/device_return.py::_set_family_return_state`、`device_aggregate_return_state`。
迁移：`u3b6c7d8e9f0`；依据：ADR-0061 D1 第 3–8 条。

### device_leases（ADR-0019）

| 字段 | 说明 |
|------|------|
| `status` | ACTIVE / RELEASED / … |
| `fencing_token` | 防旧 Agent 写 |

自 B6 激活起生效：ACTIVE 的 `MAINTENANCE` 租约是归还欠账载体，holder=`platform`、reason=`归还欠账`，
设备全部未关闭条目共用一条。JOB 在统一释放入口原地转换为 MAINTENANCE 时，id 不变，job_id 清空并推进设备租约代次。
欠账租约无自动到期；`expires_at` 的远期值只填充非空列，不作为回收依据。
原有每设备唯一 ACTIVE 索引 `uq_device_leases_active_per_device` 继续约束占用。
最后一个条目关闭或带欠账退役的同一事务释放租约；取消退役且仍有条目时同一事务重建。
代码入口：`backend/models/device_lease.py::DeviceLease`；
`backend/services/device_return.py::handle_job_lease_release`、`handle_job_lease_release_sync`、
`_convert_active_job_to_debt_lease_sync`、`_acquire_debt_lease_sync`、`release_debt_lease_for_retirement_sync`、`restore_debt_lease_on_unretire_sync`。
依据：ADR-0061 D5 第 2–4、8、10、11 条。

### device_return_debt（自 B6 激活起生效）

本表及下列行为自 B6 激活起生效；迁移 `u3b6c7d8e9f0`，ORM 为 `backend/models/device_return_debt.py::DeviceReturnDebt`。

| 字段 | 类型与含义 |
|---|---|
| `id` | integer 主键 |
| `device_id` | 非空 device 外键，设备删除时 CASCADE |
| `family` | 非空 varchar(64)，专项 |
| `source_kind` | 非空 varchar(32)，只有 `job` 或 `stock`（阶段 B 初始化）；不同于 return_state 的 source |
| `source_job_id` | nullable job_instance 外键；初始化条目可以引用历史 job，job 删除时 SET NULL |
| `source_fencing_token` | nullable varchar(256)，迟到确认核对原 job 令牌；只读条目 API 不返回该字段 |
| `cause` | 非空 varchar(64)，欠账原因；init 检查失败使用 `init_check:<正式值>` |
| `state` | 非空 varchar(32)，`open/closed` |
| `closed_reason` | nullable varchar(32)，`discharged/overridden` |
| `closed_at` | nullable timestamptz，关闭时刻 |
| `created_at` | 非空 timestamptz，建立时刻 |
| `evidence` | nullable JSONB，来源证据；设备未关闭条目 API 不返回原文 |

索引 `ix_device_return_debt_device_state` 与 `ix_device_return_debt_family_state` 服务未关闭条目查询；
部分唯一索引 `uq_device_return_debt_open_source` 在 state=open 时约束
`(device_id, family, source_kind, COALESCE(source_job_id,0))`，使无 job 来源也幂等。
控制面必须在设备行锁下串行处理条目与占用；涉及 job 时锁序为 Job → Device → Lease。
未退役设备有任一 open 条目时恰有一条 ACTIVE 租约，条目关闭后保留关闭原因与时间供审计、迟到回报与重放核对。
代码入口：`backend/services/device_return.py::_upsert_open_debt_sync`、`apply_init_check_sync`、`apply_discharge_sync`、`apply_override_sync`、`init_device_return_sync`。
依据：ADR-0061 D5 第 10、11 条；#3723 §3.4.4、§3.5 v1.7.15。

---

## 3. 脚本与调度

| 表 | 说明 |
|----|------|
| `script` | 脚本目录：`name`、`version`、`nfs_path`、`content_sha256`、`default_params` |
| `task_schedules` | Cron → `plan_id` |
| `resource_pool` | WiFi 等（`connect_wifi` 注入） |

### 用例集（ADR-0030 P1a）

| 表 | 说明 |
|----|------|
| `test_suite` | MTBF 用例集（≈ runtask.xml）：`name` 全局唯一、`project_id` 可空=通用套件、`export_dir`、`apk_binding`、`root_config`/`global_params`（JSON 保键序）、漂移比对键 `source_sha256`（导入溯源）/`exported_sha256`（runtask 磁盘）/`exported_content_sha256`（库）/`exported_global_sha256`（Global 磁盘，R05-F10 #973；升级后须重导，见 [`mtbf-api.md`](../operations/mtbf-api.md) §关键语义）、`is_active` |
| `test_case` | 用例（粒度 = testpoint）：`suite_id` CASCADE、`(suite_id, name)` 唯一、`ordinal`、`times`、`enabled`、`exec_descs` JSON（1..N 执行描述，标识符原样保留不「修正」） |

---

## 4. 用户与安全

| 表 | 说明 |
|----|------|
| `users` | 认证用户、`role`（复数历史例外见 §表名注记，#3001） |
| `revoked_refresh_token` | Refresh 黑名单 jti（ADR-0024） |
| `audit_logs` | 审计（ADR-0015；复数历史例外，#3001） |

---

## 5. 通知与去重

| 表 | 说明 |
|----|------|
| `notification_channels` / `alert_rules` | 告警（复数历史例外，#3001） |
| `notification_delivery` | **投递事实层**（#1167 P4 / ADR-0036 D6）：每行 = 一次「通知 × 通道」投递，`unique(notification_log_id, channel)`；状态词表 `requested`→`dispatched`→`accepted`/`retrying`→`failed`（向前兼容，未来加 `delivered` 不改契约）。**本表为权威**：P4 起幂等判定读本表，无本表行的历史日志才回落 `notification_logs.context.channel_delivery` JSONB |
| `plan_run_artifact` | PlanRun 级 dedup xls 等（Sprint 4 扩展） |

---

## 6. Agent 本地（SQLite）

`backend/agent/registry/local_db.py`：

| 逻辑表 | 说明 |
|--------|------|
| `log_signal_outbox` | 离线重试 |
| `watcher_state` | Watcher 游标 |
| job_archive 等 | 本地归档队列（方案 C 后简化） |

---

## 7. 枚举单一源

`backend/models/enums.py` — 前后端状态值应对齐 `frontend/src/utils/api/types.ts`。

---

## 8. 关系简图

```
Plan 1──* PlanStep
Plan 1──* PlanRun
PlanRun 1──* JobInstance
JobInstance 1──* StepTrace
JobInstance 1──* JobArtifact
JobInstance 1──* JobLogSignal
Host 1──* Device
Device 1──* device_leases
Device 1──* device_return_debt（自 B6 激活起生效）
```

---

## 9. 历史迁移

- ADR-0020 一次性迁移：Workflow* → Plan*（见 `plan_migration_audit`）  
- 旧表已删除；设计理由见 [`archive/migrations/plan-step-design-rationale.md`](../archive/migrations/plan-step-design-rationale.md)  
- 勿参考 `docs/archive/stp-spec-pre-adr0020/backend/DATABASE.md`
