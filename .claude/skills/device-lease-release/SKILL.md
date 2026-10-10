---
name: device-lease-release
description: 设备租约紧急释放——设备的 ACTIVE 租约（device_leases）没有被释放、设备无法重新租用，或平台设备页长期显示占用、必须立即让出设备时执行。触发时机：设备租约异常 / 紧急释放、PlanRun 卡住需强制让出设备、设备复用前清理租约。
type: event  # 低频事件场景（#2785 分型：HOLLOW 观察窗 60 天）
---

# 设备租约紧急释放

按顺序执行以下步骤：

1. 读取 `docs/operations/device-lease-emergency-release.md` 全文。
2. 按该文档的步骤顺序执行，不跳步。

## 踩坑守卫（负向约束）

- 本流程是**生产业务库写操作**。Agent 必须先完成文档的只读前置检查，再调用管理端释放接口。
- 当前 Requirement 没有明确授权写入生产业务库时，Agent 不得调用管理端释放接口。
- Agent 必须在调用管理端释放接口后按文档「后置验证」回查。
- Agent 不得手工 `UPDATE` `device_leases`。
- Agent 不得修改 `device` 表或 Agent 文件。
