# Spec: 审计日志系统

## REQ-1: 后端业务操作审计植入

### R1.1 tasks.py 审计植入

**覆盖函数**：`create_task`、`dispatch_task`、`cancel_task`

**前置条件**：函数签名新增 `request: Request`（用于 IP 提取）

**场景**：

```
Scenario: 用户创建任务
Given 用户已登录，发送 POST /api/v1/tasks
When create_task 成功完成 db.flush() 获得 task.id
Then record_audit 被调用:
  action="create", resource_type="task", resource_id=task.id
  details 含 name/type/tool_id/target_device_id/priority/pipeline_def_present
  db.commit() 之前调用

Scenario: 用户分发任务
Given 用户发送 POST /api/v1/tasks/{id}/dispatch，host/device 存在
When dispatch_task 完成设备锁获取
Then record_audit 被调用:
  action="dispatch", resource_type="task", resource_id=task.id
  details 含 host_id/device_id/run_id

Scenario: 用户取消任务
Given 用户发送 POST /api/v1/tasks/{id}/cancel
When cancel_task 完成状态更新
Then record_audit 被调用:
  action="cancel", resource_type="task", resource_id=task.id
  details 含 run_id/from_status/to_status
```

---

### R1.2 workflows.py 审计植入

**覆盖函数**：`create_workflow`、`start_workflow`、`cancel_workflow`、`delete_workflow`

**场景**：

```
Scenario: 创建工作流
  action="create", resource_type="workflow", details={name, steps_count}

Scenario: 启动工作流
  action="start", resource_type="workflow", details={from_status}

Scenario: 取消工作流
  action="cancel", resource_type="workflow", details={from_status, skipped_steps}

Scenario: 删除工作流
  action="delete", resource_type="workflow"
  details={name, status, steps_count} ← 在 db.delete() 之前缓存
```

---

### R1.3 tools.py 审计植入

**覆盖函数**：`create_category`、`update_category`、`delete_category`、`create_tool`、`update_tool`、`delete_tool`

**场景**：

```
Scenario: 工具分类 CRUD
  create: action="create", resource_type="tool_category", details={name, enabled}
  update: action="update", resource_type="tool_category", details={name, enabled}
  delete: action="delete", resource_type="tool_category", details={name, tools_deleted_count}
    ← tools_deleted_count 在 db.delete(category) 之前统计

Scenario: 工具 CRUD
  create: action="create", resource_type="tool", details={name, category_id, enabled, script_path}
  update: action="update", resource_type="tool", details={name, category_id, enabled}
  delete: action="delete", resource_type="tool", details={name, category_id}
    ← name/category_id 在 db.delete(tool) 之前缓存
```

---

### R1.4 notifications.py 审计植入

**覆盖函数**：`create_channel`、`update_channel`、`delete_channel`、`create_rule`、`update_rule`、`delete_rule`

**安全约束**：`details` 字段**不得**记录通知渠道的 `config` 字段（可能含 webhook token）

**场景**：

```
Scenario: 通知渠道 CRUD
  create: action="create", resource_type="notification_channel", details={name, type, enabled}
  update: action="update", resource_type="notification_channel", details={name, type, enabled}
  delete: action="delete", resource_type="notification_channel",
          details={name, type, rules_deleted_count}

Scenario: 告警规则 CRUD
  create: action="create", resource_type="notification_rule",
          details={name, event_type, channel_id, enabled}
  update: action="update", resource_type="notification_rule",
          details={name, event_type, channel_id, enabled}
  delete: action="delete", resource_type="notification_rule",
          details={name, event_type, channel_id}
```

---

### R1.5 schedules.py 审计植入

**覆盖函数**：`create_schedule`、`update_schedule`、`delete_schedule`

**场景**：

```
Scenario: 定时任务 CRUD
  create: action="create", resource_type="schedule",
          details={name, cron_expression, enabled, task_type, tool_id, target_device_id}
  update: action="update", resource_type="schedule",
          details={name, cron_expression, enabled, task_type}
  delete: action="delete", resource_type="schedule",
          details={name, cron_expression, enabled}
```

---

### R1.6 hosts.py 审计植入

**覆盖函数**：`create_host`、`update_host`（无 delete 路由，不覆盖）

**场景**：

```
Scenario: 创建主机
  action="create", resource_type="host", details={name, ip, ssh_port, ssh_auth_type}

Scenario: 更新主机
  action="update", resource_type="host", details={name, ip}
```

---

### R1.7 Agent 路由隔离（不变式）

```
Invariant: Agent 路由调用不产生审计记录
Excluded routes:
  - POST /api/v1/agent/runs/pending (GET)
  - POST /api/v1/agent/runs/{id}/heartbeat
  - POST /api/v1/agent/runs/{id}/complete
  - POST /api/v1/agent/runs/{id}/extend_lock
  - POST /api/v1/agent/logs
  - POST /api/v1/agent/runs/{run_id}/steps/{step_id}/status
```

---

## REQ-2: 时间范围过滤

### R2.1 API 层

```
Scenario: 时间范围过滤
Given GET /api/v1/audit-logs?start_time=2026-02-26T00:00:00&end_time=2026-02-26T23:59:59
Then 返回结果中所有记录满足 start_time <= timestamp <= end_time

Scenario: 仅指定起始时间
Given GET /api/v1/audit-logs?start_time=2026-02-26T00:00:00
Then 返回 timestamp >= start_time 的所有记录（end_time 无上限）

Scenario: 仅指定结束时间
Given GET /api/v1/audit-logs?end_time=2026-02-26T23:59:59
Then 返回 timestamp <= end_time 的所有记录（start_time 无下限）

Scenario: 不指定时间参数
Given GET /api/v1/audit-logs
Then 时间过滤不生效，返回全量（受 skip/limit 限制）
```

### R2.2 前端层

```
Scenario: 清空时间输入
Given 用户清空 start_time 或 end_time 输入框（值为 ""）
Then 该参数不传递给 API（等价于不过滤）

Scenario: 无效时间范围
Given start_time > end_time
Then loadLogs() 不发送请求，保持上次结果不变

Scenario: 筛选条件变更触发分页重置
Given 用户修改任意筛选条件（含 start_time/end_time）
Then setPage(0) 立即执行
```

---

## REQ-3: 侧边栏导航

```
Scenario: 审计日志入口可见
Given 用户访问平台
Then 侧边栏底部"系统管理"分组下可见"操作日志"条目（Shield 图标）

Scenario: 导航正确
Given 用户点击"操作日志"
Then 路由跳转至 /audit，AuditLogPage 正常渲染
```

---

## PBT 属性（基于属性的测试）

### 后端不变式

| 属性 | 定义 | 证伪策略 |
|------|------|---------|
| **Monotonicity** | 同一事务内 audit timestamp 单调递增 | 并发写入后按 id 排序，验证 timestamp 序列 |
| **Append-only** | `audit_logs` 表无 DELETE 操作 | 检查所有 router 无 `db.delete(AuditLog)` 调用 |
| **Completeness** | 每个覆盖的 mutation 产生恰好 1 条审计记录 | 对每个端点单独测试，验证 count = before + 1 |
| **Isolation** | Agent 路由调用不增加 audit_logs 记录数 | 调用 agent 端点前后 count 相同 |
| **User Binding** | 审计记录的 user_id 与发起请求的 JWT 用户一致 | 用不同用户 token 发起请求，验证 audit.user_id |

### 前端不变式

| 属性 | 定义 | 证伪策略 |
|------|------|---------|
| **Filter Independence** | 修改任一 filter 不影响其他 filter 值 | 设置 resource_type 后检查 start_time 不变 |
| **Pagination Reset** | 任意 filter 变更触发 page=0 | 翻页后改 filter，验证 page 归零 |
| **Empty Param Omission** | 空字符串 filter 不出现在请求 params 中 | 清空输入后检查 axios 请求的 params 对象 |
| **Temporal Guard** | start_time > end_time 时不发 API 请求 | 设置非法时间范围后验证网络请求数为 0 |
