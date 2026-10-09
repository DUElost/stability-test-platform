---
name: diagnose-device-stall
description: 设备离线 / PlanRun 卡死（界面上称「任务」卡死）的标准排障 SOP（查租约 → 查 adb server 端口 → 查心跳 → 按类处置，按序不跳步）。触发时机：设备离线或长期被占用、PlanRun 卡住、Agent 心跳正常但设备数为 0、lsusb 不识别、Hosts 页「在线 vs USB」差值异常。
---

# 设备离线 / PlanRun 卡死排障 SOP

适用条件：设备离线或长期被占用、PlanRun 卡住、Agent 心跳正常但设备数为 0、`lsusb` 不识别设备，
或 Hosts 页「在线 n」与「USB n」不一致。

> 警告：本机可能是生产控制面和生产数据库宿主。第 1–4 步只读。第 5 步起的释放租约、重启 adb server
> 和 xHCI unbind/rebind 会改变生产状态，执行者必须先确认当前 Requirement 明确授权。

## 标准作业流程（SOP）

按顺序执行以下步骤，不得跳步。跳步会把「租约未释放」误判成「设备硬件故障」。

1. 查设备的 ACTIVE 租约：执行 `docs/operations/device-lease-emergency-release.md`「前置检查」第 2–4 步
   （只读）。
2. 在 host 上运行 `pgrep -af 'fork-server server'`（经授权的 SSH，只读），查看 adb server 的实例数、
   端口和属主。
   正常结果：Linux 生产 host 只有一个实例，端口为默认 5037；WSL 联调环境端口为 5039
   （`ANDROID_ADB_SERVER_PORT=5039`）。端口配错的表现是「心跳正常但设备数为 0」；
   有两个 adb server 时主机变为 DEGRADED。
3. 在平台的设备页和主机页查看心跳更新时间。
4. 在 host 上运行 `agentctl health`（默认安装路径 `/opt/stability-test-agent/agentctl`）。
5. 如果第 1 步查到 ACTIVE 租约，且设备没有在途 PlanRun，改走 **`device-lease-release`**
   （生产写操作）。在途判据见该 SOP 文档的缺口 G2。
6. 如果出现 USB 层异常（`lsusb` 不识别、Hosts 页「在线 vs USB」差值异常或设备数为 0），先问 L0
   意图 / 作业层：向现场负责人确认这批设备是否被有意断开、关机或搬迁。平台侧没有 L0 判据，
   见 `docs/operations/host-device-visibility-triage.md` §1 L0 行。
7. 如果 L0 成立，停止本流程，不执行第 8–13 步，也不按 L1–L4 定成因。
8. 按 triage 文档 §1 判别表与 §2 只读采集命令，定位 L1 内核、L2 adb server、L3 设备 adbd 或
   L4 设备 USB 功能集。定位前不得执行第 9–13 步。
9. 如果 L1 或 L2 判据成立，确认主机空闲：主机详情接口 `GET /api/v1/hosts/{id}` 返回的
   `active_jobs` 为空列表。如果不为空，等主机空闲后再执行第 10–11 步。

> 警告：下一步解绑并重新绑定 xHCI 驱动，会断开该控制器下的全部 USB 设备。不得在主机有在跑
> job 时执行。不得只凭 `dmesg` 里的历史报错执行：日志可能是已恢复的旧事件，或不致死的
> `-71` / `-110`。

10. 如果 L1 判据**当前**成立，执行 xHCI unbind/rebind，不需要重启主机。L1 判据要同时满足：
    当前 `lsusb -t` 只剩 root hub、看不到 Hub 树与手机；`sudo dmesg -T` 有对应的 `HC died` 或
    `not responding` 主控死亡记录。判据与命令见
    `docs/operations/incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md` §2、§3.1。
    预期：`lsusb` 能看到 Hub 树与手机，而不只是 root hub。2026-09-20 两台主机验证：控制器死亡
    不会自愈，rebind 可以重复使用。

> 警告：下一步重启 adb server，会打断在途 adb 会话。L2 判据不成立时重启没有效果
> （2026-09-14 对照实验与 2026-09-20 机群实测）。

11. 如果 L2 判据成立（triage 文档 §2：ADB 接口 `ff:42` 的设备集合 ⊋ `adb devices` 列表），且第 2 步
    只看到一个 adb server 实例，运行 `adb kill-server && adb start-server`。
    预期：`adb devices` 列表与 ADB 接口设备集合相等。
12. 如果 L2 判据成立，且第 2 步看到多个 adb server 实例，不重启 server，按 triage 文档 §1 L2 行处理
    （Agent 自检，可选自愈，#160）。
13. 如果 L4 判据成立（设备在 USB 上有枚举，但没有 ADB 接口 `ff:42`；例如 MediaTek 机型的 MIDI 态
    `0e8d:2046`），在设备屏幕上把 USB 配置切回 MTP / 文件传输并开启 USB 调试。
    预期：triage 文档 §2 的 `ff:42` 探针列出该设备，`adb devices` 中该设备的 state 为 `device`。
    如果 state 为 `unauthorized` 或 `offline`，该设备转为 L3，按 triage 文档 §1 L3 行处理。

## 后置验证

1. 如果执行过释放，按 `device-lease-release` 的后置验证回查。
2. 复跑第 1 步。
   预期：查询结果中不再有第 1 步初次查到的那条 ACTIVE 租约（按租约 `id` 对照）。

## 踩坑守卫（负向约束）

- 执行者释放租约前，必须完成 `device-lease-release` 的只读前置检查，并确认当前 Requirement 明确授权
  写入。
- 非特权用户（`android`）读 `journalctl -k` / `dmesg` 会静默返回空或权限提示，这是假阴性，
  不表示没有 USB 事件。执行者必须用 sudo 取证，或改用零权限的 sysfs 判据
  （`/sys/bus/usb/devices/` 在树计数与 `ff:42` 接口，见 triage 文档 §2）。
- 「在线 n」远小于「USB n」时，多数是刷机后的 **L4 MIDI 存量**（`0e8d:2046`、无 ADB 接口，
  2026-09-14 台账在案）。此时主机侧 rebind 和 kill-server 都无效，执行者不得操作主机；
  出路是在设备侧首次开启 USB 调试。
- 执行者不得在 Linux 生产 host 上设置 `ANDROID_ADB_SERVER_PORT=5039`：该端口只用于 WSL 联调环境，
  误配会导致 DEGRADED 或设备数为 0。
