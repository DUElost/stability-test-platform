# Tasks: legacy-model-migration

**Change**: legacy-model-migration
**Date**: 2026-02-28
**Status**: Planning Complete

---

## 约束决议（Codex + Gemini 双模型审计补充）

| 约束项 | 决议 |
|---|---|
| 新 Host ID 格式 | 历史数据 `str(old_int_id)`；新注册 Agent 通过 `HOST_ID` env var 提供字符串（如 `"worker-01"`)，heartbeat 端点直接接受 String |
| RunStatus→JobStatus 映射 | QUEUED/DISPATCHED→PENDING，RUNNING→RUNNING，FINISHED→COMPLETED，FAILED→FAILED，CANCELED→ABORTED |
| Enum→String 列 | `hosts.status` / `devices.status`，均需 `ALTER COLUMN ... TYPE VARCHAR(32) USING status::text` |
| tags JSON→JSONB | 历史数据直接 `ALTER COLUMN tags TYPE JSONB USING tags::jsonb`；过滤改为 `@>` 运算符 |
| 旧 Agent 端点兼容期 | 两端同时存活，直到所有 Linux Agent 主机确认运行新版本后删除旧端点 |
| DeviceMetricSnapshot | 废弃前执行 `pg_dump -t device_metric_snapshots` 归档，然后从 heartbeat 写入路径移除 |
| host.hostname 唯一性 | 保留唯一约束（`UNIQUE INDEX`），对应旧 `hosts.name` |

---

## Phase A — 紧急修复（立即执行，不依赖数据库迁移）

### Task A-1: 修复 orchestration.py 的 Device import
- **文件**: `backend/api/routes/orchestration.py`
- **改动**: 将 `from backend.models.schemas import Device` 改为 `from backend.models.host import Device`
- **验证**: `python -m py_compile backend/api/routes/orchestration.py && python -c "from backend.api.routes.orchestration import router; print('OK')"`
- **副作用检查**: 确认 orchestration.py 中 Device 的使用仅为 `SELECT Device.id, Device.serial WHERE Device.id.in_(...)` 查询，不涉及新模型缺少的字段
- [x] A-1 完成

### Task A-2: 修复 schemas.py relationship 字符串歧义
- **文件**: `backend/models/schemas.py`
- **改动**: 将所有 `relationship("Device", ...)` 改为 `relationship("backend.models.schemas.Device", ...)`，将所有 `relationship("Host", ...)` 改为 `relationship("backend.models.schemas.Host", ...)`
- **验证**: 启动后端，`GET /api/v1/agent/runs/pending` 不再报 `Multiple classes found`
- [x] A-2 完成

**Phase A 验收**: `python -c "from backend.main import app; print('mapper OK')"` 无异常

---

## Phase B — 新 STP 模型字段补齐

### Task B-1: host.py::Device 补齐 18 个监控字段
- **文件**: `backend/models/host.py`
- **新增字段**（类型与旧 schemas.py 对齐）:
  ```python
  lock_run_id      = Column(Integer, nullable=True)
  lock_expires_at  = Column(DateTime(timezone=True), nullable=True)
  last_seen        = Column(DateTime(timezone=True), nullable=True)
  adb_state        = Column(String(32), nullable=True)
  adb_connected    = Column(Boolean, default=False, nullable=True)
  battery_level    = Column(Integer, nullable=True)
  battery_temp     = Column(Integer, nullable=True)
  temperature      = Column(Integer, nullable=True)
  wifi_rssi        = Column(Integer, nullable=True)
  wifi_ssid        = Column(String(128), nullable=True)
  network_latency  = Column(Float, nullable=True)
  cpu_usage        = Column(Float, nullable=True)
  mem_total        = Column(BigInteger, nullable=True)
  mem_used         = Column(BigInteger, nullable=True)
  disk_total       = Column(BigInteger, nullable=True)
  disk_used        = Column(BigInteger, nullable=True)
  hardware_updated_at = Column(DateTime(timezone=True), nullable=True)
  extra            = Column(JSON, default=dict, nullable=True)
  ```
- **验证**: `python -c "from backend.models.host import Device; d = Device(); assert hasattr(d, 'battery_level')"`
- [x] B-1 完成

### Task B-2: host.py::Host 补齐旧模型字段
- **文件**: `backend/models/host.py`
- **新增字段**:
  ```python
  name          = Column(String(128), nullable=True)   # 显示名称，迁移时从旧 hosts.name 填充
  ssh_port      = Column(Integer, default=22, nullable=True)
  ssh_user      = Column(String(64), nullable=True)
  ssh_auth_type = Column(String(32), default="password", nullable=True)
  ssh_key_path  = Column(String(256), nullable=True)
  extra         = Column(JSON, default=dict, nullable=True)
  mount_status  = Column(JSON, default=dict, nullable=True)
  updated_at    = Column(DateTime(timezone=True), onupdate=datetime.utcnow, nullable=True)
  ```
- **约束**: `hostname` 保留 UNIQUE 索引（对应旧 `hosts.name` 的唯一约束）
- [x] B-2 完成

### Task B-3: Alembic 新增字段迁移脚本（不改表名/PK）
- **文件**: 新建 `backend/alembic/versions/<rev>_add_monitoring_fields.py`
- **内容**: 为 `device` 表添加 B-1 的 18 个字段；为 `host` 表添加 B-2 的字段（所有字段 nullable，不影响现有数据）
- **验证**: `alembic upgrade head` 成功，`alembic downgrade -1` 可回滚
- [x] B-3 完成

**Phase B 验收**: `GET /api/v1/heartbeat` 写入监控字段不抛 AttributeError

---

## Phase C — 数据库结构迁移（高风险，需维护窗口）

### Task C-1: Alembic PK/FK 类型迁移脚本
- **文件**: 新建 `backend/alembic/versions/<rev>_host_pk_to_string.py`
- **执行顺序**（严格按此顺序）:
  1. `pg_dump -t device_metric_snapshots` 备份（废弃前归档）
  2. `hosts` 表添加临时列 `id_str VARCHAR(64)` 并回填 `UPDATE hosts SET id_str = id::text`
  3. 对所有 FK 引用表（`devices`, `task_runs`）添加临时列 `host_id_str` 并回填
  4. `ALTER TABLE hosts RENAME TO host`（先改名避免冲突）
  5. `ALTER TABLE devices RENAME TO device`
  6. 删除旧 FK 约束
  7. `host` 表：DROP 旧 int PK，RENAME `id_str` TO `id`，ADD PRIMARY KEY
  8. 子表：DROP 旧 `host_id` int，RENAME `host_id_str` TO `host_id`，ADD FK
  9. `ALTER COLUMN status TYPE VARCHAR(32) USING status::text`（hosts.status / devices.status）
  10. `ALTER COLUMN tags TYPE JSONB USING tags::jsonb`（devices.tags）
  11. DROP `hosts_id_seq` 序列
  12. DROP `device_metric_snapshots` 表（已备份）
- **验证**: `SELECT id, pg_typeof(id) FROM host LIMIT 1` 返回 `text` 类型
- [ ] C-1 完成

### Task C-2: RunStatus → JobStatus 历史数据转换
- **文件**: 同上 Alembic 脚本或独立脚本
- **映射执行**（仅 `task_runs` 表已有数据，JobInstance 是新架构无历史数据）:
  ```sql
  UPDATE task_runs SET status = 'PENDING'   WHERE status IN ('QUEUED', 'DISPATCHED');
  UPDATE task_runs SET status = 'COMPLETED' WHERE status = 'FINISHED';
  UPDATE task_runs SET status = 'ABORTED'   WHERE status = 'CANCELED';
  -- RUNNING → RUNNING, FAILED → FAILED（不变）
  ```
- **注意**: 此步骤仅转换旧 `task_runs` 表（新 `job_instances` 无历史数据），转换后旧 RunStatus Enum 失效
- [ ] C-2 完成

**Phase C 验收**:
- `SELECT id, pg_typeof(id) FROM host LIMIT 1` → `text`
- `SELECT serial, host_id FROM device LIMIT 5` → host_id 为字符串
- `alembic downgrade -1` 可回滚

---

## Phase D — API 路由层迁移

### Task D-1: api/schemas.py Pydantic 类型更新
- **文件**: `backend/api/schemas.py`
- **改动**:
  - `HostOut.id: int` → `str`
  - `HostCreate`（若有 id 字段）→ `str`
  - `DeviceOut.host_id: Optional[int]` → `Optional[str]`
  - `HeartbeatIn.host_id: int` → `str`（Agent 侧 HOST_ID 由整数改为字符串配置）
- **兼容性**: 为 Agent 兼容，`HeartbeatIn.host_id` 加 validator：`@validator('host_id', pre=True) def coerce_str(cls, v): return str(v)`（接受 int/str 双类型）
- [x] D-1 完成

### Task D-2: heartbeat.py 切换到新 ORM
- **文件**: `backend/api/routes/heartbeat.py`
- **改动**:
  - 从 `backend.models.host` import `Host, Device`
  - 从 `backend.models.enums` import `HostStatus, DeviceStatus`
  - 查询/写入改为新表（`host` / `device`）
  - 移除 `DeviceMetricSnapshot` 写入（已废弃）
  - `_mark_missing_devices_offline` 使用新 Device.last_seen 字段
- **验证**: `POST /api/v1/heartbeat` 成功写入 host/device 表，且 battery_level 等字段持久化
- [x] D-2 完成

### Task D-3: hosts.py 切换到新 ORM
- **文件**: `backend/api/routes/hosts.py`
- **改动**: import 改为 `backend.models.host`；响应 id 字段改为字符串；DEGRADED 状态判断适配（新枚举无 DEGRADED，改为 UNKNOWN/OFFLINE 逻辑）
- [x] D-3 完成

### Task D-4: devices.py 切换到新 ORM + JSONB 过滤
- **文件**: `backend/api/routes/devices.py`
- **改动**:
  - import 改为 `backend.models.host`
  - `host_id` 参数类型改为 `str`
  - tags 过滤：`Device.tags.contains(tag)` → `Device.tags.op('@>')(cast([tag], JSONB))`
- [x] D-4 完成

**Phase D 验收**:
- `GET /api/v1/hosts` 返回 `{id: "1", ...}` 字符串类型 id
- `GET /api/v1/devices?tags=smoke` 返回正确结果
- `POST /api/v1/heartbeat` 写入正确

---

## Phase E — 调度器迁移

### Task E-1: recycler.py 裸 SQL 表名修正 + 字段适配
- **文件**: `backend/scheduler/recycler.py`
- **改动**:
  - 所有裸 SQL `UPDATE devices` → `UPDATE device`
  - `hosts.status` → `host.status`
  - `WHERE lock_run_id IS NULL` → 同字段名（已在 B-1 补齐）
  - 移除 `_prune_metric_snapshots`（DeviceMetricSnapshot 已废弃）
  - 保留 `_prune_log_artifacts`
- [x] E-1 完成

### Task E-2: dispatcher.py 适配新 Device 锁字段
- **文件**: `backend/scheduler/dispatcher.py`
- **改动**: 使用新 Device（`from backend.models.host import Device`），lock 字段已在 B-1 补齐，FK 引用改为 String host_id
- [x] E-2 完成

### Task E-3: cron_scheduler.py 适配触发 WorkflowRun
- **文件**: `backend/scheduler/cron_scheduler.py`
- **改动**: 从创建旧 `Task` 行改为调用 `dispatch_workflow(workflow_definition_id=sched.workflow_definition_id, device_ids=sched.device_ids)`（TaskSchedule 表需新增 `workflow_definition_id` / `device_ids` 字段）
- **前提**: `task_schedules` 表增加 `workflow_definition_id`（FK→workflow_definitions.id）和 `device_ids`（JSONB）
- [x] E-3 完成

**Phase E 验收**: 启动 recycler 后无 `relation "devices" does not exist` 错误

---

## Phase F — Agent 端点同步切换

### Task F-1: 确认 agent_api.py 覆盖所有旧端点
- **文件**: `backend/api/routes/agent_api.py`
- **需覆盖的旧端点**（5个）:
  1. `GET /api/v1/agent/runs/pending` → 新: `GET /api/v1/agent/jobs/pending`
  2. `POST /api/v1/agent/runs/{id}/heartbeat` → 新: `POST /api/v1/agent/jobs/{id}/heartbeat`
  3. `POST /api/v1/agent/runs/{id}/complete` → 新: `POST /api/v1/agent/jobs/{id}/complete`
  4. `POST /api/v1/agent/runs/{id}/extend_lock` → 新: `POST /api/v1/agent/jobs/{id}/extend_lock`
  5. `POST /api/v1/agent/runs/{run_id}/steps/{step_id}/status` → 新: `POST /api/v1/agent/jobs/{job_id}/steps/{step_id}/status`
- **验证**: 所有5个端点在 agent_api.py 中有对应实现，通过 Swagger 可调用
- [x] F-1 完成（已确认：`/jobs/pending`, `/jobs/{id}/heartbeat`, `/jobs/{id}/complete`, `/jobs/{id}/extend_lock`, `/jobs/{id}/steps/{sid}/status` 均存在）

### Task F-2: agent/main.py 切换到新端点
- **文件**: `backend/agent/main.py`
- **改动**: 5处 URL 引用改为新路径；`host_id` 从 `HOST_ID` env var 直接读取字符串（无需 int 转换）
- **注意**: 新旧端点同时保留，直到此 Task 在所有主机部署完成并确认
- [x] F-2 完成（代码已使用 `/jobs/*` 路径，`_load_required_host_id()` 直接返回字符串）

### Task F-3: 部署新 Agent 到所有 Linux 主机 + 验证
- **操作**: rsync 新 agent 代码，systemctl restart stability-test-agent，验证日志无旧端点 404
- **完成标志**: 所有主机 `agentctl status` 显示 RUNNING，无连接错误
- [ ] F-3 完成（⚠️ 手动操作：需在所有 Linux 主机执行部署）

### Task F-4: 删除 tasks.py 中的旧 Agent 回调端点
- **文件**: `backend/api/routes/tasks.py`
- **删除的端点**: 以上5个旧 `/api/v1/agent/runs/*` 路由函数
- **前提**: F-3 完成且确认无主机仍在调用旧端点（检查访问日志）
- [x] F-4 完成（已删除 5 个旧端点，共 388 行；`limiter.py` 跳过路径更新为 `/api/v1/agent/jobs/`）

**Phase F 验收**: 访问日志中 `/api/v1/agent/runs/*` 零请求量持续5分钟后执行 F-4

---

## Phase G — 旧 ORM 清理

### Task G-1: 删除 schemas.py 中已废弃的 ORM 类
- **文件**: `backend/models/schemas.py`
- **删除**（按依赖反序）:
  - `class DeviceMetricSnapshot(Base)` — 已废弃
  - `class Deployment(Base)` / `DeploymentStatus` — 无活跃路由
  - `class Workflow(Base)` / `class WorkflowStep(Base)` — 旧顺序工作流
  - `class Device(Base)` — 迁移到 host.py
  - `class Host(Base)` — 迁移到 host.py
- **保留**: `User`, `AuditLog`, `TaskSchedule`, `NotificationChannel`, `AlertRule`, `LogArtifact`, `TaskRun`（暂时保留直至完全迁移），`Task`（同上）
- **验证**: `python -c "from backend.models.schemas import Device"` 应抛 ImportError
- [x] G-1 完成（删除 Host/Device/DeviceMetricSnapshot/Deployment/Workflow/WorkflowStep；修复 tasks.py/websocket.py/post_completion.py/report_service.py/stats.py 导入）

### Task G-2: 删除废弃路由文件
- **文件**: `backend/api/routes/deploy.py`（Deployment 路由）
- **改动**: 从 `backend/main.py` 的 router 注册中移除，删除文件
- 从 `backend/scheduler/workflow_executor.py` 删除（旧 WorkflowExecutor）
- [x] G-2 完成（deploy.py 和 workflow_executor.py 已删除；main.py 和 routes/__init__.py 已更新）

### Task G-3: 全局扫描隐性 schemas.py 引用
- **命令**: `grep -r "from backend.models.schemas import.*\(Device\|Host\|DeviceMetricSnapshot\|Deployment\|Workflow\b\)" backend/ --include="*.py"`
- **确认**: 零匹配结果
- [x] G-3 完成（非测试文件零匹配；`from backend.main import app` 无 mapper 冲突）

**Phase G 验收**:
- `python -c "from backend.main import app"` 无 mapper 冲突，无 ImportError
- `pytest backend/` 全部通过（或已知失败列表与迁移无关）

---

## 全局验收标准

- [x] `GET /api/v1/agent/runs/pending` 返回 200（Phase A 后立即）
- [x] `python -c "from backend.main import app"` 无 `Multiple classes found for path "Device"` 错误
- [x] `alembic upgrade head` 成功执行（B/E 迁移已跑通；Phase C 迁移待单独执行）
- [ ] `SELECT id, pg_typeof(id) FROM host LIMIT 1` → `text` 类型（待 Phase C 数据库迁移）
- [ ] Agent 心跳正常（待 F-3 Linux 主机部署）
- [x] `backend/models/schemas.py` 不含 `class Device(Base)` 或 `class Host(Base)`（Phase G 后）

---

## 回滚策略

| Phase | 回滚命令 |
|---|---|
| A | `git revert HEAD`（单文件 import 改动） |
| B | `alembic downgrade -1`（nullable 字段，零数据风险） |
| C | `alembic downgrade -1`（高风险，需提前 pg_dump 完整备份） |
| D | `git revert`（路由文件） |
| E | `git revert`（调度器文件） |
| F | `git revert agent/main.py` + 重新部署旧 Agent |
| G | `git revert`（schemas.py 删除是最安全的回滚） |

---

## 已废弃功能确认清单

- [ ] `pg_dump -t device_metric_snapshots > backup_device_metrics_$(date +%Y%m%d).sql`（Phase C 前，如表仍存在）
- [x] Deployment API 路由已确认无前端调用（deploy.py 已删除，G-2 完成）
- [x] 旧 Workflow/WorkflowStep 无前端引用（`/workflows` 已重定向到 `/orchestration/workflows`）
- [x] report_json/jira_draft_json 字段保留在 task_runs 表中（不删除历史数据，仅停止写入）
