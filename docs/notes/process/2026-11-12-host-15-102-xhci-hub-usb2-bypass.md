# `.102` xHCI 主控恢复后 Hub USB3 分支失效的 USB2 上行旁路

Status: implemented
Class: process

## Decision

1. 在 [`docs/operations/host-device-visibility-triage.md`](../../operations/host-device-visibility-triage.md)
   新增 §5「L1 恢复后：单个 Hub 的 USB3 分支失效与 USB2 上行旁路」，给出只读判据、
   处置步骤与 2026-11-12 `.102` 实例；原「相关」节改为 §6。
2. §1 L1 单元格补指针：主控 rebind 恢复后单个 Hub 的 SuperSpeed 分支仍反复枚举失败时按 §5
   处置。
3. §1 L4 段补一句边界：关机态设备同样以 `0e8d:2046` 出现且没有 ADB 接口，`adb_interfaces_missing`
   在这类批次上是预期结果，执行者必须先按 L0 确认设备是否关机/空置。
4. 旁路定位为**过渡处置**，不是修复：这类 Hub 是单一整机，内部级联结构不可拆分或改造，
   执行者只能选「换上行口」或「整只更换」，终态是更换该 Hub。

## Alternatives

- 只把处置写进单次事故复盘：放弃。`-71/-110` 累计与大面积枚举失败在 fleet 上反复出现
  （2026-11-09 `.93`、2026-07-29 `8.87`），复盘文档不在四层判别的检索路径上，排障时不会被读到。
- 建议整机 reboot 或反复 rebind：放弃。`.102` 的 rebind 已救回整树，剩余失败固定在
  单个 Hub 的 SuperSpeed 分支；rebind 改变不了 Hub 侧 SS PHY 的过流状态。
- 建议减少 Hub 级联层数：放弃。该 Hub 是单一整机，`1-5` 下的分级由内部结构决定，
  现场不可改造（2026-11-12 现场确认）；能动的只有上行口与整只更换。
- 直接更换 Hub 后再记录：放弃。更换需要采购与停机窗口，而现场先用现有纯 USB2 口即可恢复
  设备可见性；文档先固化旁路与验证口径，更换仍为终态。

## Verification

- 切换前 `uhubctl`：`2-5`（`0bda:0411`）端口状态 `fffffffc ... oc`，随后该 Hub 从列表消失；
  `1-5`（`0bda:5411`）与 15 台关机态 `0e8d:2046` 稳定。
- 切换后 `lsusb -t`：Bus 002 下无 hub 子树，`1-5.x` 全部重新枚举；50 秒内 `dmesg` 无新的
  `2-5` 枚举失败或 `-71`（使用只读的 `stp-agent-priv read-kernel-log` 取证）。
- 平台侧：`capacity.usb_device_count` 从切换瞬态的 5 回到 15；`health.reasons` 只剩
  `adb_interfaces_missing`（关机态预期），`usb_link_degraded` 待 1 小时窗口滑过后回落。
- 文档门禁：`tools/dev/check-internal-ip-leak.py` 与 `scripts/run_gates.py gov-surface`
  在本次改动上通过（内网主机以 `.102` 简称引用，不写四段完整地址）。
- 舰队核对（2026-11-12，只读）：另三台报 `usb_link_degraded` 的 `.42/.60/.75` 的
  `-71/-110` 全部是单个设备的 `Failed to suspend device`，`lsusb` 设备数 17/8/16、
  24 小时零 `USB disconnect`，不属本节形态；`.54` 与 `.102` 同形（`2-5` 24 小时 12,313 次
  重新枚举，USB2 侧 `1-5` 与内部五个 `0bda:5411` 正常，Bus 002 只有 root hub）。

## Revisit

- 若更换 Hub 后同一 `Bus.Port` 仍反复枚举失败，说明故障面在上级级联而非该 Hub，此时 §5 判据
  第 3 条（Bus 002 无其他可用子树）不再成立，需要重新分层。
- 若 `.54` 按 §5 处置后恢复，说明该形态可复制；若同形主机继续增加，再评估在 Agent 侧增加
  「单 Bus.Port 重复枚举」计数，把该判据从人肉取证变成信号。仅含 autosuspend `-110` 的主机
  不得计入该信号，否则会把 `usb_link_degraded` 的既有噪声放大成告警。
