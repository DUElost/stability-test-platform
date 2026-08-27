---
name: device-lease-release
description: 设备租约紧急释放——设备卡在 ACTIVE 无法重新租用、或平台设备页长期被占用需要立即让出时执行。触发时机：设备租约异常/紧急释放、PlanRun 卡住需强制让出设备、设备复用前清理租约。
---

# 设备租约紧急释放

权威 SQL 见根 `CLAUDE.md` §开发陷阱（原文即此，无歧义）。两步执行，先查后改。

## 1. 前置确认（只读）

```sql
SELECT id, device_id, status, leased_at, released_at
FROM device_leases
WHERE device_id = '<设备ID>' AND status = 'ACTIVE';
```

- 确认确有一条 `ACTIVE` 租约、且该设备当前无在途 PlanRun 引用（有则优先走 PlanRun 侧释放，紧急情况除外）。

## 2. 执行释放（写操作，权限受限时交由管理员执行）

```sql
UPDATE device_leases
SET status = 'RELEASED', released_at = now()
WHERE device_id = '<设备ID>' AND status = 'ACTIVE';
```

## 3. 后置验证

```sql
SELECT id, device_id, status, released_at
FROM device_leases
WHERE device_id = '<设备ID>'
ORDER BY id DESC LIMIT 3;
```

- 最新一条应为 `RELEASED` 且 `released_at` 为刚才时刻。
- 若设备仍显示占用：检查心跳/heartbeat 侧是否在续租（ADR-0019 机制），必要时同步查设备在线状态。

## 约束

- 只清 `device_leases` 租约行，**不触碰** `device` 表与任何 Agent 侧文件。
- 表名单数：`device_leases` 非 `device_leases` 之外的任何复数变体。
