# `.75` SuperSpeed 分支半枚举致 uhubctl 每次固定 +27s：判据补半枚举形态与软件 `remove` 解法

Status: implemented
Class: process

## Decision

1. 在 [`docs/operations/host-device-visibility-triage.md`](../../operations/host-device-visibility-triage.md)
   §5.1 给判据 1 增加**半枚举形态**：`lsusb -t` 里某一级 Hub 显示 `Driver=[none]/0p`、
   内核持续打印 `hub <Bus.Port>:1.0: hub_ext_port_status failed (err = -110)`，此时可以完全没有
   `new SuperSpeed USB device` 重枚举风暴。执行者不得因缺重枚举风暴而排除判据 1。
2. §5.1 增加**故障对象判定**要求：内核的 `usb <X>: Failed to suspend device` 这行 Hub 也会打印，
   执行者看到 `usb_link_degraded` 时必须先用 `bDeviceClass`（`09` 即 Hub）判定对象是 Hub 还是
   终端设备，不得据此行认定手机侧。
3. 收窄 §5.1 末句「没有这个形状的主机不得按本节处置」为「对象是终端设备且判据 1、2 均不成立时
   才不得按本节处置」。原句是 2026-11-12 把 `.75` 放过的直接成因，本次按勘误处理并在 §5.3 留痕。
4. §5.2 第 3 步后补强制约束：USB2 上行旁路只搬走在用的那根上行，**残留的失效上行必须一并摘掉**，
   否则它继续留在 USB 设备列表里污染 `uhubctl`。摘除动作写成新的 §5.4 SOP（现场拔线或软件
   `remove` 二选一），`.75` 实例记进 §5.5。
5. §5.4 把软件 `remove` 的前置判据写成硬性步骤：只允许对 **root hub 的直接子设备**使用，
   且必须先确认该子树不承载任何在用设备。理由：`remove` 的 port-disable 请求这时是发给 root hub
   的；若对深端失效设备使用，拆除路径自己也会卡满内核控制超时。同时写明它 write-once、
   软件侧不可撤销，恢复只能重插线或 rebind xHCI（后者会连带重新枚举另一条总线上的在用设备）。
6. §5.4 记录 `uhubctl` 的耗时特征作为排障口径：它与 `-l`/`-p` 收窄范围无关（每次都会查询系统里
   所有 Hub），因此该耗时**不能**用来判断故障对象，也不影响纯 sysfs 路径。执行者据此把
   「取证工具慢」与「在用 Hub 坏了」分开。
7. 本次不动 `backend/agent/contracts/kernel_usb_faults.py` 的词表与阈值，不动
   `backend/agent/stp_agent_priv.py` 的 `USB_POWER_TIMEOUT_SECONDS`。

## Alternatives

- 提高 `USB_POWER_TIMEOUT_SECONDS`（60→120）吸收这段固定开销：放弃。那是拿超时预算掩盖硬件故障，
  且该常量属 ADR-0037 D8 的边界面、wrapper 逐台经 `update_agent.yml` 下发，代价与收益不匹配。
- 在词表里把 Hub 对象产生的 `-110` 排除出 `usb_link_degraded`：放弃。`.75` 恰恰证明这类计数对应
  一只真坏的 Hub；照此调整等于把要暴露的故障调成静默，也与本次收严判据的方向相反。
- 用 `unbind`（`/sys/bus/usb/drivers/usb/unbind`）替代 `remove`：放弃，实测无效。`unbind` 只解除
  驱动绑定，设备仍留在 USB 设备列表里，`uhubctl` 照样打开并查询它。`.75` 上最具说服力的反证是：
  最深层那只 Hub 本来就没绑上 hub 驱动（`Driver=[none]`），而它正是 `uhubctl` 阻塞时 fd 指向的对象。
- 直接现场拔线作为唯一处置：本次 Owner 选择先在软件层解决（该机 SS 分支自 2026-10-21 起未承载
  任何设备，拔线窗口另排）；§5.2 第 6 条「更换 Hub」的终态出口保持不变，§5.4 明确标注 `remove`
  只停止软件面污染、不修硬件。
- 首选 rebind xHCI 清障：放弃。它会把同一控制器上另一条总线（承载 16 台在用手机的那条）一起
  重新枚举，且 §1 L1 已记录 rebind 无效需 reboot 的先例；只作为 `remove` 之后要恢复该分支时的
  手段写进 §5.4 警告。
- 只把这次写成单次事故复盘：放弃。理由同 2026-11-12 那次——复盘文档不在四层判别的检索路径上，
  而误判恰好发生在检索路径上的判据句里。

## Verification

- 判据来自实测而非推演。取证全程只读（`/proc/<pid>` 的 `state`/`wchan`/`fd`、sysfs、
  `read-kernel-log`）：`uhubctl` 各形态 22~31s 而 `user+sys` 合计 0.02~0.06s；阻塞时
  `state=D`、`wchan=usb_start_wait_urb`，fd 指向失效分支最深层 Hub 的设备节点；同机
  `lsusb` 0.011s；单口定向调用（含 `-d 1`）26.8s，证明耗时与请求端口数无关。
- 半枚举形态的证据：`lsusb -t` 该分支最深层 `Driver=[none]/0p`、`bDeviceClass=09`；
  15 分钟内核窗内 `hub_ext_port_status failed` 67 次、`Failed to suspend device` 68 次，
  对象均为 Hub；同窗在用侧 0 条、重枚举 0、`USB disconnect` 0 ⇒ 判据 1 的原形态不成立、
  新增形态成立。
- §5.4 的处置按其自身步骤执行并验证（Owner 授权后）：`remove` 写入耗时 20.6s；之后
  `uhubctl` 全量 1.30s、定向 0.30s；`lsusb` 设备总数 28→23（等于摘掉的 5 只 Hub）；
  在用侧 16 台设备串号集合逐条 diff 一致、`adb` 口径与 `capacity.usb_device_count=16` 未变；
  内核不再出现失效侧任何行。
- `uhubctl` 无 CLI 级规避手段已实测排除：v2.6.0-1 对 `-b` 与 `-D` 均报 `invalid option`（rc=1）。
  此前一次「`-D` 只要 0.01s」的读数是参数报错被 `2>/dev/null` 吞掉的假象——该次输出 0 只 Hub、
  0 台设备。判据句因此不承诺任何过滤选项。
- 顺带查到的配置漂移（另案，见 §5.5 末条）：当日 11:30 Ansible 写入的 VBUS 槽位映射仍指向
  改插前的顶级口号；19:47:37 该树已改插（sysfs 目录 mtime 为证），11 条映射口的 sysfs 目录
  全部不存在，`usb-power` 在查表阶段即 fail-closed（不会打到别的口）。逐条比对证明 11/11 只有
  顶级口号变化、子 Hub 与端口号不变。另有 5 台在用设备从来不在映射里。
- **未完成**：`usb_link_degraded` 的回落需等 1 小时统计窗滑过，本次尚未复测到位；
  同期报 `usb_link_degraded` 的 `.42`、`.60` 未复核（§5.3 只勘误 `.75`）；fleet 其余主机是否
  存在同一半枚举形态未普查。

## Revisit

- 若普查发现多台主机呈同一半枚举形态，再评估把「Hub 对象 `-110`」与「终端设备 `-110`」
  在 Agent 侧分桶计数。先决条件是分桶不得削弱 `usb_link_degraded` 对真故障的灵敏度——
  2026-11-12 那条「仅含 autosuspend `-110` 的主机不得计入该信号」的告诫仍然有效，
  因为本次证明「autosuspend `-110`」本身就可能是 Hub。
- 若某主机 `remove` 之后 `uhubctl` 仍然慢，说明在用侧也有 Hub 不响应控制请求，§5.1 的
  分层要重做，不得继续按「失效分支」解释。
- 若后续 `uhubctl` 升级提供按 bus / VID:PID 过滤的能力，§5.4 应退化为 CLI 级规避
  （不碰 sysfs、不产生 write-once 副作用），届时本节与 §5.4 一并改写。
- `.75` 的槽位映射重登记与那 5 台无 VBUS 槽位的设备，在 Ansible 控制机上完成后，
  执行者必须复跑 §5.2 第 4 条与 §5.4 第 4 步做闭环验收；本文不代替 host_vars 的变更记录。
