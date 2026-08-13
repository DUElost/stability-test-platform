# 2026-07-29 192.0.2.87 xHCI 主控死亡与 ADB 全空复盘

> **状态**：USB 子系统已恢复（2026-08-05 11:06 xHCI driver unbind/rebind，**未整机 reboot**）。恢复后暴露双 ADB server 设备拆分问题，见 [GitHub #160](https://github.com/DUElost/stability-test-platform/issues/160)（与本次 xHCI 事故**不同根因**）。
> **影响范围**：单台 Agent host（`192.0.2.87`，Dell OptiPlex 3090，Service Tag B3G64R3），约 **7 天**内该 host 上全部 USB 设备对内核不可见，`adb devices` 恒为空，Agent 上报 `online_healthy_devices=0`。控制面与其他 host 无影响；该 host 历史设备记录仍保留在 DB（17 条 OFFLINE，`last_seen` 停在 2026-07-29）。
> **关联**：[9.126 硬挂事故](./incident-2026-07-28-host-9-126-hard-hang-and-bios-upgrade.md) §4.2（本机属 **C 组出厂 BIOS**）、[GitHub #160](https://github.com/DUElost/stability-test-platform/issues/160)（恢复后的 ADB 多 server 拆分）。

---

## 1. 现象与时间线

| 时刻（host CST, UTC+8） | 事件 |
|-------------------------|------|
| Jul 9 14:14 | 当前 boot 启动（`who -b`）。至事故前连续在线约 20 天。 |
| Jul 29 17:30:02 … 17:30:20 | USB Hub 树反复 enumerate / disconnect；`Failed to suspend device, error -110`、Hub `0bda:0411` 级联抖动。 |
| Jul 29 17:30:31 | **xHCI 主控死亡**：`xHCI host controller not responding, assume dead` → `HC died; cleaning up`；Bus 1/2 上全部 USB 设备批量 disconnect（含 Hub 树与手机）。 |
| Jul 29 17:30 … Aug 5 11:05 | **不可观测窗口（USB 层）≈ 7 天**。`lsusb` 仅余 2 个 root hub；`adb devices` 为空。Agent 服务 **仍 active**、心跳正常，但 `discovered_devices: 0`、`online_healthy_devices: 0`。 |
| Aug 4 21:48 | 运维 SSH 排查：确认非 ADB 配置问题，根因在 USB 主控层（见 §2）。 |
| Aug 5 11:05:54 | 执行 xHCI **driver unbind/rebind**（方案 A，见 §3.1），未 reboot。 |
| Aug 5 11:06:04 … 11:06:09 | xHCI 重新注册，Hub 树与 16 台手机（`19d2:1352`）重新 enumerate。 |
| Aug 5 11:06+ | `lsusb` 恢复 30 行（含 Hub）；`adb devices` 非空。Agent 开始上报部分设备；随后发现 5037/5039 双 server 拆分 → #160。 |

> **与 9.126 硬挂的区别**：本次 **整机未挂**——SSH、Agent 心跳、journald 均正常；仅 **USB 子系统**在软件层面进入 dead 状态。journal 不会在 cron 行瞬间截断，也无 boot 标 `crash`。

---

## 2. 根因判定

判定结论：**Intel xHCI USB 3.1 主控死亡**（`0000:00:14.0` Comet Lake xHCI），导致内核无法枚举任何 USB 设备；**不是 ADB 软件配置、udev 或 USB 调试授权问题**。

判据：

| 检查项 | 结果 | 说明 |
|--------|------|------|
| `dmesg` 致命行 | **有** | `xhci_hcd 0000:00:14.0: xHCI host not responding to stop endpoint command` → `assume dead` → `HC died; cleaning up` |
| 事故后至恢复前 USB 事件 | **无** | Jul 29 17:30:31 之后至 Aug 5 unbind 之前，`dmesg` 无新的 USB enumerate 记录 |
| `lsusb` | 仅 2 行 root hub | 无 Hub、无手机；与内核状态一致 |
| `adb devices`（5037 / 5039） | 均为空 | ADB 无设备可枚举——因 USB 层已空 |
| Agent / `plugdev` / `ADB_PATH` | 正常 | `android` 在 `plugdev` 组；Agent active；`.env` 中 `ADB_PATH=adb` |
| 整机 / 网络 / Agent 进程 | 正常 | 非 9.126 类 board-level hard hang |

崩溃前征兆（Jul 29 17:30 前数秒）：

```
usb 2-5.4: Failed to suspend device, error -110
usb 2-5.1: Device not responding to setup address.
usb 2-5.1: device descriptor read/all, error -110
usb 2-5-port1: couldn't allocate usb_device
```

Hub 型号为级联 `0bda:0411`（Generic 4-Port USB 3.1 Hub）+ `0bda:5411`（RTS5411），多机并联拓扑对 xHCI 与 Hub 供电敏感。

排除清单：

- **非 ADB 端口/权限问题**：主控死亡期间两个端口的 `adb devices` 均为空；恢复 USB 后才出现设备。
- **非 Agent 代码崩溃**：Agent 持续心跳，仅设备发现为空。
- **非「手机未插好」**：Hub 树整体从内核消失，属主控级故障。
- **非控制面/UI 问题**：SSH 实机 `adb devices` 与 Agent 日志均为 0。

---

## 3. 已执行的处置措施（仅作用于 192.0.2.87）

### 3.1 xHCI driver unbind / rebind（2026-08-05，首选）

**目标**：在不 reboot 的前提下重新初始化 xHCI 主控，恢复 USB 枚举。

```bash
# 在 192.0.2.87 上，需 root
sudo sh -c 'echo 0000:00:14.0 > /sys/bus/pci/drivers/xhci_hcd/unbind'
sleep 2
sudo sh -c 'echo 0000:00:14.0 > /sys/bus/pci/drivers/xhci_hcd/bind'
```

验证：

```bash
# 应看到 Hub 树与手机，而非仅 root hub
lsusb -t
lsusb -d 19d2:1352 | wc -l    # 2026-08-05 恢复后为 16

adb devices -l                  # 应非空（需 USB 调试已授权）
```

`dmesg` 应出现 bind 后 `new USB bus registered` 与手机 `New USB device found`（Unisoc / nubia A57）。

> **说明**：本次 **方案 A 有效**；整机 reboot 同样可恢复，但对 Agent 任务中断面更大。若 unbind/rebind 失败，再 `sudo reboot`。

### 3.2 恢复后暴露的次要问题（#160）

USB 恢复后，同一 host 上存在 **5037 + 5039 两个 `adb fork-server`**，16 台手机被拆成 10 + 6；Agent（默认 5037）仅上报 10 台。详见 [#160](https://github.com/DUElost/stability-test-platform/issues/160)。**不属于 xHCI 事故本身**，但会在恢复操作后叠加出现，排查时需区分。

临时规避（ops，待 #160 代码修复前）：

```bash
kill $(pgrep -f 'adb.*fork-server')
adb start-server
# 若 Agent 需固定端口，在 /opt/stability-test-agent/.env 显式设置 ANDROID_ADB_SERVER_PORT 并 restart agent
```

---

## 4. 与同 fleet 其他 host 的关联

本机在 [9.126 事故 §4.2 BIOS 清单](./incident-2026-07-28-host-9-126-hard-hang-and-bios-upgrade.md#42-完整-bios-清单) 中：

| IP | 机型 | BIOS | 备注 |
|----|------|------|------|
| 192.0.2.87 | OptiPlex 3090 | **2.4.0**（2022-04-09） | ⚠ 出厂 BIOS，**C 组高风险**；`fwupd` 未安装 |

与 9.126（7090，已升 BIOS 1.42.0 + watchdog/EEE 加固）不同，**8.87 尚未落地 §3 任一加固项**。长期在线 + 老 BIOS + 多 USB Hub 级联，与本次 xHCI 死亡模式一致（子系统级失效，非整机硬挂）。

> **决策沿用 9.126 §5 冻结策略**：其余 19 台 host 的 watchdog / EEE / BIOS **不主动推进**；本节仅作知识留档。若多台 3090 再现 `HC died`，再按机型灰度评估是否复用 9.126 加固包 + USB 监控。

---

## 5. 后续建议（未执行 · 知识留档）

| 项 | 说明 |
|----|------|
| **监控 `dmesg` / journal** | 对 Agent host 告警 `HC died` 或 `xhci_hcd.*not responding`，触发人工或自动 unbind/rebind / reboot |
| **USB Hub 供电与拓扑** | 减少 `0bda:0411` 级联层数；Hub 独立供电；避免热插拔风暴 |
| **BIOS / fwupd** | 8.87 仍为 2.4.0；与 9.126 同属 Dell 老固件风险组，升级需 drain + reboot，见 9.126 §5 |
| **Agent 侧** | #160：启动时确保单一 ADB server，避免恢复后设备拆分 |
| **运维手册** | 遇 `adb devices` 空且 `lsusb` 无手机：先 `lsusb -t`，再查 `dmesg \| grep -i xhci`，再决定 unbind/rebind 或 reboot |

---

## 6. 回退路径

### 6.1 xHCI unbind/rebind

无独立「回退」——若 bind 后 USB 仍异常，**直接 reboot**：

```bash
sudo reboot
```

unbind 期间 SSH（网卡）不受影响；仅 USB 子设备短暂离线。

### 6.2 误杀 ADB server

```bash
adb start-server
# 或 systemctl restart stability-test-agent
```

---

## 7. 复盘要点

- **`adb devices` 为空 ≠ ADB 配置错**：必须先对比 `lsusb` 与 `dmesg`。内核看不到 USB 时，ADB 必然为空。
- **xHCI `HC died` 后不会自愈**：主控死亡后 Hub/手机不会自动回来；需 **unbind/rebind 或 reboot**，单纯拔插手机无效。
- **Agent 心跳正常不能证明设备在线**：`HeartbeatThread` 依赖 `adb devices -l`；USB 层空窗期内控制面应看到 `online_healthy_devices=0`，属正确反映。
- **与硬挂文档并列留档**：9.126 = 整机 hard hang；8.87 = USB 子系统 xHCI death。二者均可发生在老 BIOS、长期在线的 Dell Agent host 上，判据与处置不同。
- **恢复顺序**：① 恢复 USB（本章 §3.1）→ ② 确认单一 ADB server（#160）→ ③ 再核对 Agent `discovered_devices` 与 `lsusb` 手机数一致。
