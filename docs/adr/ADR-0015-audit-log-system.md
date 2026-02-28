# ADR-0015: 审计日志系统
- 状态：Accepted
- 优先级：P1
- 目标里程碑：M2
- 日期：2026-02-25
- 决策者：平台研发组
- 标签：审计, 安全, 合规, 用户行为追踪

## 背景

平台需要具备审计能力，记录关键操作以满足安全合规要求：
1. 追踪用户操作行为（创建、修改、删除、启动、取消等）
2. 支持按资源类型、操作类型、用户进行筛选
3. 仅管理员可访问审计日志
4. 支持分页查询

## 决策

### 数据模型

`AuditLog` 表结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer | 主键 |
| user_id | Integer (FK) | 操作用户 ID |
| username | String | 用户名（冗余存储） |
| action | String | 操作类型（create/update/delete/start/cancel 等） |
| resource_type | String | 资源类型（task/workflow/tool/notification 等） |
| resource_id | Integer | 资源 ID |
| details | JSON | 操作详情 |
| ip_address | String | 客户端 IP |
| timestamp | DateTime | 操作时间 |

### 索引优化

- `ix_audit_user_ts`：用户 ID + 时间戳（支持按用户查询）
- `ix_audit_resource`：资源类型 + 资源 ID（支持按资源追踪）

### API 端点

- `GET /api/v1/audit-logs` - 分页获取审计日志列表
  - Query 参数：`resource_type`, `action`, `user_id`, `skip`, `limit`
  - 需要管理员权限

### 前端页面

- `AuditLogPage.tsx` - 审计日志查看页面
  - 支持分页浏览
  - 支持按资源类型、操作类型筛选
  - 非管理员用户静默忽略（无权限提示）

## 备选方案与权衡

- 方案 A：使用外部审计服务（如 Elasticsearch）
  - 优点：功能丰富，查询性能好
  - 缺点：引入外部依赖，部署复杂
- 方案 B：数据库审计表（当前决策）
  - 优点：无外部依赖，与平台紧耦合
  - 缺点：查询性能受限，大数据量需分表/归档

## 影响

- 正向影响：满足安全合规要求，支持问题追溯
- 需在关键操作点植入审计日志记录逻辑
- 定期归档历史数据以控制表体积

## 落地与后续动作

- ✅ AuditLog 数据模型定义
- ✅ 审计日志 API（只读）
- ✅ 前端 AuditLogPage
- ⏳ 在关键业务操作点植入审计记录
- ⏳ 审计日志归档策略

## 关联实现/文档

### 后端
- `backend/models/schemas.py` - AuditLog 模型定义
- `backend/api/routes/audit.py` - 审计日志 API
- `backend/api/schemas.py` - AuditLogOut Schema

### 前端
- `frontend/src/pages/audit/AuditLogPage.tsx` - 审计日志页面

### 数据库
- `audit_logs` 表
- 索引：`ix_audit_user_ts`, `ix_audit_resource`
