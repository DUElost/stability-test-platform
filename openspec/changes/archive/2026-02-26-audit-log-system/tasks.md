# Tasks: 审计日志系统

> 所有任务均为零决策——按顺序机械执行即可，无需做任何技术选择。

---

## T1: tasks.py 审计植入

**文件**: `backend/api/routes/tasks.py`

**准备工作**:
- 在文件顶部 import 列表追加：`from backend.core.audit import record_audit`
- 确认 `from fastapi import ..., Request` 已导入（当前已有）

### T1.1 create_task

**函数签名变更**：在参数列表末尾添加 `request: Request`

```python
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    request: Request = None,   # 新增
):
```

**插入位置**：在 `db.commit()` 调用之前（第一次 commit），在 `db.flush()` 生成 `task.id` 之后

```python
db.flush()   # 已有或新增，确保 task.id 可用
record_audit(
    db,
    action="create",
    resource_type="task",
    resource_id=task.id,
    details={
        "name": payload.name,
        "type": payload.type,
        "tool_id": payload.tool_id,
        "target_device_id": resolved_target_device_id,
        "priority": payload.priority,
        "pipeline_def_present": payload.pipeline_def is not None,
    },
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

### T1.2 dispatch_task

**函数签名变更**：在参数列表末尾添加 `request: Request = None`

**插入位置**：在设备锁获取成功、`db.commit()` 之前

```python
record_audit(
    db,
    action="dispatch",
    resource_type="task",
    resource_id=task.id,
    details={
        "host_id": payload.host_id,
        "device_id": payload.device_id,
        "run_id": run.id,
    },
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

### T1.3 cancel_task

**函数签名变更**：在参数列表末尾添加 `request: Request = None`

**插入位置**：状态更新完成后、`db.commit()` 之前

```python
record_audit(
    db,
    action="cancel",
    resource_type="task",
    resource_id=task.id,
    details={
        "run_id": run.id if run else None,
        "from_status": prev_task_status,
        "to_status": TaskStatus.CANCELED.value,
    },
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

---

## T2: workflows.py 审计植入

**文件**: `backend/api/routes/workflows.py`

**准备工作**：追加 `from backend.core.audit import record_audit`；所有目标函数添加 `request: Request = None` 参数

### T2.1 create_workflow

```python
db.flush()  # 确保 wf.id 可用
record_audit(
    db,
    action="create",
    resource_type="workflow",
    resource_id=wf.id,
    details={"name": wf.name, "steps_count": len(payload.steps)},
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

### T2.2 start_workflow

```python
# prev_status = wf.status（在状态变更之前捕获）
record_audit(
    db,
    action="start",
    resource_type="workflow",
    resource_id=wf.id,
    details={"from_status": prev_status},
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

### T2.3 cancel_workflow

```python
# prev_status = wf.status（在状态变更之前捕获）
# skipped_count = 被跳过的 step 数量
record_audit(
    db,
    action="cancel",
    resource_type="workflow",
    resource_id=wf.id,
    details={"from_status": prev_status, "skipped_steps": skipped_count},
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

### T2.4 delete_workflow

```python
# 在 db.delete(wf) 之前缓存信息
wf_name = wf.name
wf_status = wf.status.value
wf_steps_count = len(wf.steps)
db.delete(wf)
record_audit(
    db,
    action="delete",
    resource_type="workflow",
    resource_id=workflow_id,
    details={"name": wf_name, "status": wf_status, "steps_count": wf_steps_count},
    user_id=current_user.id,
    username=current_user.username,
    request=request,
)
db.commit()
```

---

## T3: tools.py 审计植入

**文件**: `backend/api/routes/tools.py`

**准备工作**：追加 `from backend.core.audit import record_audit`；所有目标函数添加 `request: Request = None`

### T3.1 create_category

```python
db.add(category)
db.flush()
record_audit(
    db, action="create", resource_type="tool_category", resource_id=category.id,
    details={"name": category.name, "enabled": category.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T3.2 update_category

```python
# 在字段赋值之后
record_audit(
    db, action="update", resource_type="tool_category", resource_id=category.id,
    details={"name": category.name, "enabled": category.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T3.3 delete_category

```python
# 在 db.delete(category) 之前
cat_name = category.name
tool_count = db.query(Tool).filter(Tool.category_id == category_id).count()
db.delete(category)
record_audit(
    db, action="delete", resource_type="tool_category", resource_id=category_id,
    details={"name": cat_name, "tools_deleted_count": tool_count},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T3.4 create_tool

```python
db.add(tool)
db.flush()
record_audit(
    db, action="create", resource_type="tool", resource_id=tool.id,
    details={"name": tool.name, "category_id": tool.category_id,
             "enabled": tool.enabled, "script_path": tool.script_path},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T3.5 update_tool

```python
record_audit(
    db, action="update", resource_type="tool", resource_id=tool.id,
    details={"name": tool.name, "category_id": tool.category_id, "enabled": tool.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T3.6 delete_tool

```python
tool_name = tool.name
tool_category_id = tool.category_id
db.delete(tool)
record_audit(
    db, action="delete", resource_type="tool", resource_id=tool_id,
    details={"name": tool_name, "category_id": tool_category_id},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

---

## T4: notifications.py 审计植入

**文件**: `backend/api/routes/notifications.py`

**准备工作**：追加 `from backend.core.audit import record_audit`；所有目标函数添加 `request: Request = None`

**安全约束**：`details` 不含 `config` 字段（可能含 token/secret）

### T4.1 create_channel

```python
db.add(channel)
db.flush()
record_audit(
    db, action="create", resource_type="notification_channel", resource_id=channel.id,
    details={"name": channel.name, "type": channel.type, "enabled": channel.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T4.2 update_channel

```python
record_audit(
    db, action="update", resource_type="notification_channel", resource_id=channel.id,
    details={"name": channel.name, "type": channel.type, "enabled": channel.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T4.3 delete_channel

```python
ch_name = channel.name
ch_type = channel.type
rules_count = db.query(AlertRule).filter(AlertRule.channel_id == channel_id).count()
db.delete(channel)
record_audit(
    db, action="delete", resource_type="notification_channel", resource_id=channel_id,
    details={"name": ch_name, "type": ch_type, "rules_deleted_count": rules_count},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T4.4 create_rule

```python
db.add(rule)
db.flush()
record_audit(
    db, action="create", resource_type="notification_rule", resource_id=rule.id,
    details={"name": rule.name, "event_type": rule.event_type,
             "channel_id": rule.channel_id, "enabled": rule.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T4.5 update_rule

```python
record_audit(
    db, action="update", resource_type="notification_rule", resource_id=rule.id,
    details={"name": rule.name, "event_type": rule.event_type,
             "channel_id": rule.channel_id, "enabled": rule.enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T4.6 delete_rule

```python
rule_name = rule.name
rule_event = rule.event_type
rule_channel = rule.channel_id
db.delete(rule)
record_audit(
    db, action="delete", resource_type="notification_rule", resource_id=rule_id,
    details={"name": rule_name, "event_type": rule_event, "channel_id": rule_channel},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

---

## T5: schedules.py 审计植入

**文件**: `backend/api/routes/schedules.py`

**准备工作**：追加 `from backend.core.audit import record_audit`；所有目标函数添加 `request: Request = None`

### T5.1 create_schedule

```python
db.add(sched)
db.flush()
record_audit(
    db, action="create", resource_type="schedule", resource_id=sched.id,
    details={"name": sched.name, "cron_expression": sched.cron_expression,
             "enabled": sched.enabled, "task_type": sched.task_type,
             "tool_id": sched.tool_id, "target_device_id": sched.target_device_id},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T5.2 update_schedule

```python
record_audit(
    db, action="update", resource_type="schedule", resource_id=sched.id,
    details={"name": sched.name, "cron_expression": sched.cron_expression,
             "enabled": sched.enabled, "task_type": sched.task_type},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T5.3 delete_schedule

```python
sched_name = sched.name
sched_cron = sched.cron_expression
sched_enabled = sched.enabled
db.delete(sched)
record_audit(
    db, action="delete", resource_type="schedule", resource_id=schedule_id,
    details={"name": sched_name, "cron_expression": sched_cron, "enabled": sched_enabled},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

---

## T6: hosts.py 审计植入

**文件**: `backend/api/routes/hosts.py`

**准备工作**：追加 `from backend.core.audit import record_audit`；`create_host`、`update_host` 添加 `request: Request = None`

### T6.1 create_host

```python
db.add(host)
db.flush()
record_audit(
    db, action="create", resource_type="host", resource_id=host.id,
    details={"name": host.name, "ip": host.ip,
             "ssh_port": host.ssh_port, "ssh_auth_type": host.ssh_auth_type},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

### T6.2 update_host

```python
# 在字段赋值之后
record_audit(
    db, action="update", resource_type="host", resource_id=host.id,
    details={"name": host.name, "ip": host.ip},
    user_id=current_user.id, username=current_user.username, request=request,
)
db.commit()
```

---

## T7: audit.py 时间范围过滤

**文件**: `backend/api/routes/audit.py`

**变更**：

1. 导入行追加 `from datetime import datetime`（若未导入）
2. `list_audit_logs` 签名添加两个参数：
   ```python
   start_time: Optional[datetime] = Query(None),
   end_time: Optional[datetime] = Query(None),
   ```
3. 在现有 `action` filter 之后追加：
   ```python
   if start_time:
       query = query.filter(AuditLog.timestamp >= start_time)
   if end_time:
       query = query.filter(AuditLog.timestamp <= end_time)
   ```

---

## T8: api.ts 签名扩展

**文件**: `frontend/src/utils/api.ts`

**定位**：找到 `audit: {` 块，替换 `list` 方法实现：

```typescript
audit: {
  list: (
    skip = 0,
    limit = 50,
    filters?: {
      resource_type?: string;
      action?: string;
      user_id?: number;
      start_time?: string;
      end_time?: string;
    }
  ) => {
    const params: Record<string, any> = { skip, limit };
    if (filters) {
      Object.entries(filters).forEach(([k, v]) => {
        if (v !== '' && v !== undefined) params[k] = v;
      });
    }
    return apiClient.get<PaginatedResponse<any>>('/audit-logs', { params });
  },
},
```

---

## T9: AuditLogPage.tsx 时间范围筛选

**文件**: `frontend/src/pages/audit/AuditLogPage.tsx`

### T9.1 扩展 filters 状态

```typescript
const [filters, setFilters] = useState({
  resource_type: '',
  action: '',
  start_time: '',
  end_time: '',
});
```

### T9.2 loadLogs 时间校验

在 `setLoading(true)` 之后，`apiClient.get` 之前添加：

```typescript
if (filters.start_time && filters.end_time && filters.start_time > filters.end_time) {
  setLoading(false);
  return;
}
```

### T9.3 loadLogs 参数传递

将现有 params 构建逻辑改为：

```typescript
const params: any = {};
if (filters.resource_type) params.resource_type = filters.resource_type;
if (filters.action) params.action = filters.action;
if (filters.start_time) params.start_time = filters.start_time;
if (filters.end_time) params.end_time = filters.end_time;
const res = await api.audit.list(page * pageSize, pageSize, params);
```

### T9.4 时间输入 JSX

在现有两个 `<select>` 之后追加（位于 `</div>` 闭合之前）：

```tsx
<input
  type="datetime-local"
  value={filters.start_time}
  onChange={(e) => { setFilters({ ...filters, start_time: e.target.value }); setPage(0); }}
  className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
/>
<input
  type="datetime-local"
  value={filters.end_time}
  onChange={(e) => { setFilters({ ...filters, end_time: e.target.value }); setPage(0); }}
  className="px-3 py-2 border border-gray-200 rounded-lg text-sm"
/>
```

### T9.5 useEffect 依赖更新

```typescript
useEffect(() => { loadLogs(); }, [page, filters]);
// filters 对象已包含 start_time/end_time，无需单独添加依赖
```

---

## T10: Sidebar.tsx 导航入口

**文件**: `frontend/src/layouts/Sidebar.tsx`

### T10.1 导入 Shield 图标

在现有 `import { ..., AlertCircle } from 'lucide-react';` 行中追加 `Shield`：

```typescript
import {
  ...,
  AlertCircle,
  Shield,   // 新增
} from 'lucide-react';
```

### T10.2 追加导航分组

在 `navGroups` 数组末尾（"分析报告"分组之后）追加：

```typescript
{
  label: '系统管理',
  items: [
    { path: '/audit', label: '操作日志', icon: Shield },
  ],
},
```

---

## 实施顺序建议

```
T7 (audit.py filter) → T8 (api.ts) → T9 (AuditLogPage) → T10 (Sidebar)
                                                   ↑ 可并行
T1 → T2 → T3 → T4 → T5 → T6 (均独立，可并行实施)
```

**后端 T1-T6 可并行执行**（各文件独立，无相互依赖）
**前端 T8-T10 可并行执行**（T8 是 T9 的依赖，但修改量小，可先完成）
