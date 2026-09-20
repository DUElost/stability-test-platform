# Host 设备可见性与「在线 vs USB」差值排查（四层判别）

适用症状：Hosts 页「设备 / 任务」列的 **在线 n**（ADB 口径）与 **USB n**（lsusb 口径）
不一致、在线掉到 0、或同批设备从 `adb devices` 消失。

两个数由 Agent **同一心跳 tick** 采集（`backend/agent/heartbeat_thread.py` 的
`online_healthy` 与 `count_usb_devices()`），所以差值不是快照滞后，而是设备/主机
某一层真实状态不同的信号。列的设计动机与口径见
[`../notes/feature/2026-09-11-hosts-usb-device-count.md`](../notes/feature/2026-09-11-hosts-usb-device-count.md)。

排查顺序：**先分层定位，再动手**。四层中只有 L2 属于主机侧，其余三层都在设备侧或
内核侧——误判方向会让「重启 adb server」这类无效动作变成默认反射。

## 1. 四层判别表

| 层 | 典型观察 | 只读判据 | 处置 |
|---|---|---|---|
| **L1 内核 / USB 子系统** | `lsusb`/sysfs 里手机整体变少或为空，`USB n` 同时掉 | `sudo dmesg -T` 出现 `xHCI host not responding` / `HC died; cleaning up` / `error -71(-110)` | xHCI driver unbind/rebind 或 reboot；完整判据见 [8.87 事故复盘](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md) |
| **L2 主机 adb server** | 设备在 USB 上**有 ADB 接口**，`adb devices` 却少或为空 | 「ADB 接口设备集合 ⊋ adb 列表集合」（§2 probe）；`pgrep -af 'fork-server server'` 出现多实例 / 非 5037 端口 / 非 Agent 属主 | 多 server（#160）由 Agent 自检并可选自愈；**单 server 卡死**才用 `adb kill-server && adb start-server`（打断在途 adb 会话，须先确认无在跑任务） |
| **L3 设备 adbd / 授权** | `adb devices` 能列出该设备，但 state 是 `offline` / `unauthorized` | 两侧集合相等（无 L2 漏项），且存在非 `device` 行 | `adb reconnect offline`、重插、设备侧确认授权或重启；属**设备侧**，server 重启无效 |
| **L4 设备 USB 功能集** | `USB n > 0` 而在线为 0 或明显偏低 | sysfs 接口里**没有** `ff:42`；`.../1.0/interface` 返回 `MIDI function`（`0e8d:2046`） | 只能设备侧人工：屏幕上把 USB 配置切回 MTP/文件传输并开启 USB 调试；adb 侧一切操作无效 |

PID/接口名是 MLD-LX3 等 MediaTek 机型 2026-09-14 实测口径：`0e8d:201c` 暴露
`ff:42` 且 iInterface=`ADB Interface`（adb 可见，正常态）；`0e8d:2046` 暴露
`01:01`（`MIDI function`）+`01:03`，**没有 ADB 接口，任何 adb server 都看不到**。
注意 `backend/agent/scripts/flash_firmware/*` 注释把 2046 称作「普通态手机」——那里的
含义是「非刷机态」，不代表有 ADB 接口。

## 2. 只读采集命令（在 host 上，经授权的 SSH）

```bash
adb devices -l                                    # L3：看 state
pgrep -af 'fork-server server'                    # L2：server 实例 / 端口 / 属主
sudo dmesg -T | grep -iE 'xhci|error -71|error -110'   # L1

# L2 判据：列出所有暴露 ADB 接口(ff:42)的设备，与 adb devices 的 serial 做差集
for f in /sys/bus/usb/devices/*:*.*; do
  [ "$(cat "$f/bInterfaceClass" 2>/dev/null)" = ff ] || continue
  [ "$(cat "$f/bInterfaceSubClass" 2>/dev/null)" = 42 ] || continue
  d=$(basename "$f"); echo "${d%%:*}"
done | sort -u

# L4 判据：接口名字段
cat /sys/bus/usb/devices/<dev>:1.0/interface      # "MIDI function" = MIDI-only
```

判断规则：

- `接口集合 == adb 列表集合` → 无 L2；继续看 state（L3）与接口类型（L4）；
- `接口集合 ⊋ adb 列表集合` → **L2 成立**，此时 server 重启才是对症动作；
- `接口集合 == 空` 且 `USB n > 0` → L4（设备没有 ADB 接口），adb 侧无解。

## 3. 平台侧已有信号与缺口

已有：

- `USB n` 徽标（`capacity.usb_device_count`，`frontend/src/components/network/ExpandableHostTable.tsx`）——L4 在页面上唯一可见的信号；
- `adb_multiple_servers`（warning 级 reason → DEGRADED，`backend/agent/capacity_reporter.py:156`），配套自愈 `ensure_single_adb_server()`（`backend/agent/device_discovery.py:178`，需 `STP_ADB_AUTO_REPAIR=1` 且无在跑任务）；
- **L1 的两个内核判据已落地（#2900）**：`usb_host_controller_dead`（内核报 `HC died` / `xHCI … not responding` **且此刻 USB 一台都看不到** ⇒ DEGRADED；设备回树自动回落）与 `usb_link_degraded`（窗口内 `error -71/-110` 或「cable is bad」超阈）。实现：`backend/agent/kernel_usb_faults.py`（低频读 `journalctl -k`，首扫读整段 boot，读不到按「未知」不报）。
  ⚠ **这两条的前提是 agent 能读到内核日志**：Agent 服务 `User=android`（`backend/agent/install_agent.sh`）且安装脚本从未把它加进 `adm`/`systemd-journal`；非特权 `journalctl -k` 退出码 0、stdout 只有 `-- No entries --`（与「内核干净」同形），而 `kernel.dmesg_restrict=1` 也堵死了 `dmesg`/`/dev/kmsg` 备用路。⇒ 现网这类 host 上两条 reason **恒不出现**，其通道态由 `capacity.usb_kernel_log` 单独上报（`ok`/`unavailable`/`unknown`），覆盖缺口自身有告警 `StabilityUsbKernelLogChannelDark`；裁决与三条路线见 #2957。**在这条通道被授权之前，L1 的人肉判据仍然是上机 `journalctl -k | grep -E 'xhci|HC died'`（§2）**。
- 刷机链路的同类记录：[`firmware-requests/2026-08-26-persist-sys-usb-config-adb.md`](./firmware-requests/2026-08-26-persist-sys-usb-config-adb.md)（刷完 userdata 清空 → adbd 不启动 → `adb devices` 连 unauthorized 都不显示 → 需人工开一次 USB 调试）。

**#2902 起（2026-09-20）：L2/L3/L4 在平台侧可直接判别**（全部为观测面，不参与槽位/门禁计算）：

- `capacity.adb_interface_count` — sysfs 里暴露 ADB 接口（`ff:42`）的设备数
  （`device_discovery.count_adb_interface_devices`，纯只读文件扫描；读不到 → `null` 未知，
  不当 0——否则制造假 L4）；
- `capacity.adb_state_counts` — `adb devices` 按 state 分桶
  （`device` / `offline` / `unauthorized` / `other`，只统计 adb 可见行）；
- 判别规则（与 §2 的人工商口径同构）：
  - `adb_interface_count == 0` 且 `usb_device_count > 0` → **L4**（设备无 ADB 接口）；
  - `adb_interface_count > adb_state_counts.device` → **L2**（主机 adb server 漏项）；
  - `adb_state_counts` 的非 `device` 桶非空 → **L3**（设备侧 adbd / 授权）；
- **新增 `usb_tree_empty` reason**（warning → DEGRADED）：`usb_device_count ≤ root hub 数`
  且 `discovered_devices == 0`——覆盖「整树死亡时 `total_devices == 0` 使
  `adb_low_healthy_devices` 短路」的盲区（.63 / 8.87 形态不再恒显 HEALTHY）；
  root hub 数只作判据输入、不上报（心跳 payload 增幅实测 98B，预算 <100B）。

遗留（仅记录）：

- adb 一台都枚举不到且 USB 树仍在（`usb_device_count > root hub 数`）的中间形态，
  仍需按 §2 上机差分定位（`total_devices == 0` 且**有**外设时既非空树也非 adb 全死）。
- L1 的告警半边已接（#2900）：`/metrics` 拉取期把 `health.reasons` 折成
  `stability_host_health_reason{host_id,reason}`，规则 `StabilityHostUsbControllerDead`
  （critical）与 `StabilityHostUsbLinkDegraded`（warning）在
  `deploy/prometheus/alerts-stability-platform.yml`；词表四处一致性由
  `tests/test_host_health_reason_surface.py` 对拍。**但中心侧规则文件仍是人工副本**
  （`docs/operations/README.md` §6：随仓库更新需人工重放 + `POST /-/reload`，ADR-0011
  正式挂载未落地）——合并 ≠ 生效。

## 4. 2026-09-14 实证

- 全 fleet 48 台中 **15 台不一致**（差值合计 119 台）。逐台探针结果：
  **15 台全部满足「ADB 接口集合 == adb 列表集合」，无一例 `接口 ⊋ 列表`**，每台只有
  1 个 5037 fork-server，DB 侧 0 台上报 `adb_multiple_servers`、0 台 DEGRADED
  → 当日**不存在 L2 实例**。
- 归因分布：≈93 台 **L4**（无 ADB 接口；如 192.0.2.65 的 18 台全为 MIDI-only、
  192.0.2.70 的 18 台中 17 台），≈26 台 **L3**（如 192.0.2.59 的 20 条里 5 台 offline、
  192.0.2.81 的 16 条里 13 台 offline）。
- **kill/start-server 对照实验**（5 台：192.0.2.65=L4、192.0.2.66=L4 混合、
  192.0.2.59/192.0.2.81=L3、192.0.2.61=健康对照）：`adb kill-server` → `adb start-server`
  后**枚举结果逐台不变**（连 offline 的 5/13 台也原样保留），server PID 已换新。
  无副作用：心跳 7–14s 内恢复、对照机 20/20；且 kill 后 ≤2s server 即被 Agent 下一
  tick 自动拉回。
  结论：**重启 adb server 不是通用恢复手段**，仅在 L2 判据成立时才是。

## 5. 相关

- 列的设计与口径：[`notes/feature/2026-09-11-hosts-usb-device-count.md`](../notes/feature/2026-09-11-hosts-usb-device-count.md)
- 只读诊断边界与凭据来源：[`production-diagnostics.md`](./production-diagnostics.md)
- xHCI 死亡致 USB/ADB 全空：[`incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md)
- HONOR / MLD 刷机 runbook：[`honor-flash-runbook.md`](./honor-flash-runbook.md)
