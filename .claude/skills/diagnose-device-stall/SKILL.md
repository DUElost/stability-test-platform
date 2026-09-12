---
name: diagnose-device-stall
description: 设备掉线 / 任务卡死的标准排障 SOP（查租约 → 查 ADB 端口 → 查心跳 → 释放租约，按序不跳步）。触发时机：设备离线或长期被占用、PlanRun 卡住、Agent 心跳正常但设备数为 0。
---

# 设备掉线 / 任务卡死排障 SOP

## 执行前置检查

- [ ] 确认现象口径：设备页 / 心跳 / 租约三者先分别看，不要先动数据库
- [ ] 本机可能是生产宿主——**只读查询优先**，写操作需明确授权

## 标准作业流程（SOP，按序执行，不跳步）

1. **查租约**（只读）：
   ```sql
   SELECT id, device_id, status, leased_at, released_at
   FROM device_leases
   WHERE device_id = '<设备ID>' AND status = 'ACTIVE';
   ```
   上下文与完整口径见 `docs/operations/device-lease-emergency-release.md`。
2. **查 ADB 端口**：WSL 联调环境必须 `ANDROID_ADB_SERVER_PORT=5039`；Linux 生产
   host 用默认 **5037**（误配表现为「心跳正常但设备数为 0」）；双 ADB server 会
   DEGRADED——`adb kill-server` 后统一 5037。
3. **查心跳**：设备页 / 主机页心跳新鲜度；Agent 侧 `agentctl health`。
4. **定性并按类处置**：
   - 租约未释放（且无在途 PlanRun）→ 走 **`device-lease-release`**（生产写操作，
     需当前请求明确授权）；
   - 设备硬件 / 驱动异常 → 参考
     `docs/operations/incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`。

## 后置验证

- 复跑步骤 1 的查询确认租约终态；设备页可重新租用 / 复用；
- 若执行过释放：按 `device-lease-release` 的后置回查。

## 踩坑守卫（负向约束）

- **跳步会把「租约未释放」误判成「设备硬件故障」**——SOP 顺序不可跳；
- 释放租约是生产业务库写操作：先只读确认、确认无在途 PlanRun、且当前请求明确授权；
- `ANDROID_ADB_SERVER_PORT=5039` 仅 WSL 联调；Linux 生产 host 误配会 DEGRADED /
  设备数为 0。
