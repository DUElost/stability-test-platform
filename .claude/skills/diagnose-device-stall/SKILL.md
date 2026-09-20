---
name: diagnose-device-stall
description: 设备掉线 / 任务卡死的标准排障 SOP（查租约 → 查 ADB 端口 → 查心跳 → 释放租约，按序不跳步）。触发时机：设备离线或长期被占用、PlanRun 卡住、Agent 心跳正常但设备数为 0、lsusb 不识别、Hosts 页「在线 vs USB」差值异常。
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
   DEGRADED。**kill-server 仅在 L2 判据成立时对症**（`ff:42` ADB 接口设备集合
   ⊋ `adb devices` 列表，只读探针见
   `docs/operations/host-device-visibility-triage.md` §2）——09-14 对照实验与
   09-20 fleet 实测（.20/.65/.70/.87/.66）证明 L2 不成立时重启 server 零效果。
3. **查心跳**：设备页 / 主机页心跳新鲜度；Agent 侧 `agentctl health`。
4. **定性并按类处置**：
   - 租约未释放（且无在途 PlanRun）→ 走 **`device-lease-release`**（生产写操作，
     需当前请求明确授权）；
   - **USB 层异常（lsusb 不识别 / 「在线 vs USB」差值 / 设备数为 0）→ 先按
     `docs/operations/host-device-visibility-triage.md` 四层判别（L1 内核 ·
     L2 server · L3 adbd · L4 功能集）定位，再动手**；
   - L1 判据成立（`dmesg` 见 `HC died` / xHCI 风暴）→ 空闲窗（`active_jobs=0`）
     xHCI unbind/rebind 即可救、无需 reboot（命令与判据见
     `docs/operations/incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`
     §3.1；.102/.63 于 09-20 双验证，死亡不自愈、rebind 可反复用）。

## 后置验证

- 复跑步骤 1 的查询确认租约终态；设备页可重新租用 / 复用；
- 若执行过释放：按 `device-lease-release` 的后置回查。

## 踩坑守卫（负向约束）

- **跳步会把「租约未释放」误判成「设备硬件故障」**——SOP 顺序不可跳；
- 释放租约是生产业务库写操作：先只读确认、确认无在途 PlanRun、且当前请求明确授权；
- **非特权用户（android）读 `journalctl -k` / `dmesg` 会静默返回空或权限提示——是
  假阴性不是「无 USB 事件」**：取证走 sudo，或改用零权限 sysfs 判据
  （`/sys/bus/usb/devices/` 在树计数 + `ff:42` 接口，见 triage 文档 §2）；
- 「在线 ≪ USB n」多为刷机后 **L4 MIDI 存量**（`0e8d:2046`、无 ADB 接口，
  如 .20/.65/.70/.87/.66，09-14 台账在案）——主机侧 rebind / kill-server 均无效，
  勿动主机，出路是设备侧首开 USB 调试；
- `ANDROID_ADB_SERVER_PORT=5039` 仅 WSL 联调；Linux 生产 host 误配会 DEGRADED /
  设备数为 0。
