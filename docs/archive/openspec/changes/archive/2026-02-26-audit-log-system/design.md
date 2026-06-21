# Design: 审计日志系统

## 1. 技术决策总览

| 决策点 | 选定方案 | 原因 |
|--------|----------|------|
| 调用位置 | `record_audit()` 在 `db.commit()` 之前、业务逻辑完成之后 | 保证审计与业务在同一事务中，原子提交 |
| IP 提取 | 传入 `request: Request` 给 `record_audit()` | `backend/core/audit.py` 已封装 X-Forwarded-For 提取逻辑 |
| 用户上下文 | `user_id=current_user.id, username=current_user.username` | 所有目标路由已有 `current_user` 依赖，无需改签名 |
| Agent 操作 | 不植入审计 | Agent 路由用 `verify_agent_secret` 鉴权，频率高、噪音大 |
| 时间范围解析 | FastAPI `Query(Optional[datetime])` 自动解析 ISO-8601 | 避免手工 `fromisoformat`，保持与 AuditLog.timestamp UTC-naive 一致 |
| 前端日期状态 | 合并到现有 `filters` state 对象 | 保持筛选逻辑统一，分页重置逻辑复用 |
| 空值处理 | `api.ts` 中用 `Object.fromEntries` 过滤空字符串和 undefined | 避免后端收到空字符串被解析为非法 datetime |

---

## 2. 后端设计

### 2.1 record_audit 调用模式

```python
# 标准模式（所有 mutation 路由统一遵循此顺序）
from backend.core.audit import record_audit

# 1. 完成业务变更（db.add / 字段赋值）
# 2. db.flush()  ← 获取自增 ID（若需要）
# 3. record_audit(db, action=..., resource_type=..., ...)  ← 内部 flush
# 4. db.commit()  ← 事务提交（业务 + 审计 原子）
```

### 2.2 需要新增 `request: Request` 参数的路由

以下路由当前签名无 `Request`，需添加：

| 路由文件 | 函数 | 当前签名缺 Request? |
|---------|------|------------------|
| `tasks.py` | `create_task` | ✅ 需要添加 |
| `tasks.py` | `dispatch_task` | ✅ 需要添加 |
| `tasks.py` | `cancel_task` | ✅ 需要添加 |
| `workflows.py` | 全部 mutation | ✅ 需要添加 |
| `tools.py` | 全部 mutation | ✅ 需要添加 |
| `notifications.py` | 全部 mutation | ✅ 需要添加 |
| `schedules.py` | 全部 mutation | ✅ 需要添加 |
| `hosts.py` | `create_host`、`update_host` | `list_hosts` 已有 Request；mutation 函数需添加 |

> `from fastapi import Request` 已在各路由文件中导入或可直接添加。

### 2.3 `details` 内容规范

**原则**：只记录可辅助问题定位的字段，不记录密钥/密码/完整配置对象。

| resource_type | action | details 字段 |
|---|---|---|
| `task` | `create` | `name`, `type`, `tool_id`, `target_device_id`, `priority`, `pipeline_def_present` |
| `task` | `dispatch` | `host_id`, `device_id`, `run_id` |
| `task` | `cancel` | `run_id`, `from_status`, `to_status` |
| `workflow` | `create` | `name`, `steps_count` |
| `workflow` | `start` | `from_status` |
| `workflow` | `cancel` | `from_status`, `skipped_steps` |
| `workflow` | `delete` | `name`, `status`, `steps_count` |
| `tool_category` | `create/update/delete` | `name`, `enabled` (delete 额外含 `tools_deleted_count`) |
| `tool` | `create/update/delete` | `name`, `category_id`, `enabled`, `script_path` |
| `notification_channel` | `create/update/delete` | `name`, `type`, `enabled` (不含 config 明文) |
| `notification_rule` | `create/update/delete` | `name`, `event_type`, `channel_id`, `enabled` |
| `schedule` | `create/update/delete` | `name`, `cron_expression`, `enabled`, `task_type` |
| `host` | `create` | `name`, `ip`, `ssh_port`, `ssh_auth_type` |
| `host` | `update` | `name`, `ip`（变更后值） |

### 2.4 REQ-2 时间范围过滤（`audit.py`）

```python
from datetime import datetime

@router.get("")
def list_audit_logs(
    resource_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    start_time: Optional[datetime] = Query(None),   # 新增
    end_time: Optional[datetime] = Query(None),     # 新增
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    ...
):
    ...
    if start_time:
        query = query.filter(AuditLog.timestamp >= start_time)
    if end_time:
        query = query.filter(AuditLog.timestamp <= end_time)
```

### 2.5 hosts.py 说明

当前 `hosts.py` 无 delete 路由 → REQ-1 对 Host 覆盖 `create` + `update`（不含 delete）。

---

## 3. 前端设计

### 3.1 `AuditLogPage.tsx` 状态扩展

```tsx
// filters state 合并时间范围
const [filters, setFilters] = useState({
  resource_type: '',
  action: '',
  start_time: '',   // 新增，格式: "YYYY-MM-DDTHH:mm"
  end_time: '',     // 新增，格式: "YYYY-MM-DDTHH:mm"
});
```

**行为规则（不可违背）：**
- 任意 filter 字段变更 → 立即 `setPage(0)`
- `end_time < start_time` → `loadLogs()` 提前 return（不发请求）
- 空字符串 → 不传该参数给 API

### 3.2 时间筛选 JSX 位置

放置在现有两个 `<select>` 之后，同行 `flex gap-3`。

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

### 3.3 `api.ts` 签名扩展

```typescript
audit: {
  list: (
    skip = 0,
    limit = 50,
    filters?: {
      resource_type?: string;
      action?: string;
      user_id?: number;
      start_time?: string;  // 新增
      end_time?: string;    // 新增
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

### 3.4 `Sidebar.tsx` 变更

```tsx
// 1. 导入新增 Shield
import { ..., Shield } from 'lucide-react';

// 2. navGroups 末尾追加
{
  label: '系统管理',
  items: [
    { path: '/audit', label: '操作日志', icon: Shield },
  ],
},
```

---

## 4. 不在本期范围内

- `hosts.py` delete 路由（该路由目前不存在）
- `workflows.py` update 路由（该路由目前不存在）
- 审计日志归档/清理策略（ADR-0015 标注为后续动作）
- 批量操作（`/tasks/batch/*`）的审计
