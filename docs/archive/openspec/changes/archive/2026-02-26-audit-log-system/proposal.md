# Proposal: 审计日志系统 (Audit Log System)

## Context

稳定性测试管理平台已具备审计日志的**基础骨架**（ADR-0015），包括：
- `AuditLog` ORM 模型（`backend/models/schemas.py:461`）
- `record_audit()` 辅助函数（`backend/core/audit.py`）
- `GET /api/v1/audit-logs` 只读 API 端点（管理员权限）
- 前端 `AuditLogPage.tsx`（分页 + 资源类型/操作类型筛选）
- 前端路由 `/audit` 已注册

**尚未落地的 ADR 遗留项**（⏳ 标注）：
1. **业务操作点未植入审计调用** — `record_audit()` 从未在任何路由处理器中被调用
2. **时间范围筛选缺失** — API 和前端均无 `start_time`/`end_time` 参数
3. **侧边栏无入口** — `Sidebar.tsx` 未包含审计日志导航项

## Problem Statement

当前平台所有业务操作（创建任务、修改工作流、删除工具、变更通知规则等）均无审计记录，无法满足：
- **安全合规**：无法追溯"谁在什么时间做了什么"
- **故障排查**：无法通过操作历史定位异常变更的源头
- **权限审计**：无法验证操作是否在授权范围内发生

## Requirements

### REQ-1: 后端业务操作审计植入

在以下资源的 mutation 操作完成后，**在同一事务内**调用 `record_audit()`：

| 资源 | 路由文件 | 覆盖操作 |
|------|----------|---------|
| Task | `tasks.py` | `create`、`dispatch`、`cancel` |
| Workflow | `workflows.py` | `create`、`update`、`delete`、`start`、`cancel` |
| Tool & ToolCategory | `tools.py` | `create`、`update`、`delete`（含分类） |
| NotificationChannel & AlertRule | `notifications.py` | `create`、`update`、`delete` |
| TaskSchedule | `schedules.py` | `create`、`update`、`delete` |
| Host | `hosts.py` | `create`、`delete` |

**Constraints:**
- Agent 触发的操作（心跳、任务完成）**不记录**审计（频率高、噪音大）
- 所有目标路由均已包含 `current_user: User = Depends(get_current_active_user)` 依赖，无需修改路由签名
- 调用位置：在 `db.commit()` 之前，`db.flush()` 之后；`record_audit()` 内部已包含 `db.flush()`
- `details` 字段应记录有意义的变更摘要（如被修改的字段，或关联的 device_id/host_id）

### REQ-2: 时间范围筛选

**API 层**（`backend/api/routes/audit.py`）：
- 新增 Query 参数：`start_time: Optional[datetime]`、`end_time: Optional[datetime]`
- 使用 `AuditLog.timestamp >= start_time`、`AuditLog.timestamp <= end_time` 过滤

**前端层**（`frontend/src/pages/audit/AuditLogPage.tsx`）：
- 新增 `<input type="datetime-local">` × 2（开始时间、结束时间）
- 与现有 resource_type、action 过滤器并排放置
- 参数名：`start_time`、`end_time`（ISO 字符串格式，与 API 一致）

**API Client**（`frontend/src/utils/api.ts`）：
- `api.audit.list()` 增加 `start_time?: string`、`end_time?: string` 参数

### REQ-3: 侧边栏导航入口

在 `frontend/src/layouts/Sidebar.tsx` 的 `navGroups` 末尾新增分组：

```ts
{
  label: '系统管理',
  items: [
    { path: '/audit', label: '操作日志', icon: Shield },
  ],
}
```

- 导入 `Shield` 图标（lucide-react，已在 `AuditLogPage.tsx` 中使用）
- 位置：现有最后一组（"分析报告"）之后

## Success Criteria

1. **审计记录生成验证**：创建一个任务 → `audit_logs` 表出现对应 `action=create, resource_type=task` 的记录，包含操作用户和 IP
2. **查询过滤验证**：
   - `GET /api/v1/audit-logs?resource_type=task` 仅返回任务相关记录
   - `GET /api/v1/audit-logs?start_time=T1&end_time=T2` 仅返回时间范围内记录
3. **权限验证**：非管理员访问 `GET /api/v1/audit-logs` 返回 403
4. **前端可见性**：侧边栏出现"操作日志"入口，管理员可访问，页面正常展示记录并支持时间范围筛选
5. **Agent 静默验证**：Agent 完成任务时 `audit_logs` 表无新增记录

## Constraints Summary

### Hard Constraints
- `record_audit()` 必须在同一 DB 事务中调用（当前实现：`db.flush()` + 外部 `db.commit()`）
- 审计表不可删除（无 DELETE 端点）
- 仅管理员可读取审计日志
- Agent 路由使用 `verify_agent_secret`，非用户鉴权，**不植入审计**

### Soft Constraints
- `resource_type` 命名与前端下拉选项保持一致（`task`, `workflow`, `tool`, `tool_category`, `notification_channel`, `notification_rule`, `schedule`, `host`）
- `action` 命名遵循现有约定（`create`, `update`, `delete`, `dispatch`, `start`, `cancel`）
- `details` 优先记录资源标识字段（name, id, 关联 ID）

### Dependencies & Sequencing
1. 后端审计植入（REQ-1）可独立完成
2. 时间范围筛选（REQ-2）需同时修改 backend + frontend（原子性）
3. 侧边栏导航（REQ-3）可独立完成

### Risks
- 高频路由（如批量操作）可能在单次请求中产生多条审计记录 — 可接受，符合设计意图
- `tasks.py` 内 Agent 专用端点（`/agent/runs/...`）使用 `verify_agent_secret`，需明确不植入
