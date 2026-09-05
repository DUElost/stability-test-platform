# 设备租约紧急释放

仅在设备卡在 ACTIVE、PlanRun 正常释放路径不可用且必须立即复用设备时使用。该操作
直接写业务库，执行前必须确认当前 Requirement 明确授权。

## 前置查询

```sql
SELECT id, device_id, status, leased_at, released_at
FROM device_leases
WHERE device_id = '<设备ID>' AND status = 'ACTIVE';
```

确认只有目标 ACTIVE 租约，且设备没有在途 PlanRun 引用；能走 PlanRun 正常释放时，
不要使用本流程。

## 释放

```sql
UPDATE device_leases
SET status = 'RELEASED', released_at = now()
WHERE device_id = '<设备ID>' AND status = 'ACTIVE';
```

## 后置验证

```sql
SELECT id, device_id, status, released_at
FROM device_leases
WHERE device_id = '<设备ID>'
ORDER BY id DESC
LIMIT 3;
```

最新记录应为 RELEASED。设备仍显示占用时，检查 Agent heartbeat 是否仍在续租，以及
设备在线状态。

只修改 `device_leases`；不得触碰 `device` 表或 Agent 文件。数据库凭据与生产写操作
边界见 [`production-diagnostics.md`](./production-diagnostics.md)。
