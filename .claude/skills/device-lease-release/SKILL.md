---
name: device-lease-release
description: 设备租约紧急释放——设备卡在 ACTIVE 无法重新租用、或平台设备页长期被占用需要立即让出时执行。触发时机：设备租约异常/紧急释放、PlanRun 卡住需强制让出设备、设备复用前清理租约。
---

# 设备租约紧急释放

读取并严格执行
`docs/operations/device-lease-emergency-release.md`。

这是生产业务库写操作：先做只读查询，确认没有可用的 PlanRun 正常释放路径，并在当前
请求明确授权写入后才能执行 UPDATE。完成后必须按文档回查；不得修改 `device` 表或
Agent 文件。
