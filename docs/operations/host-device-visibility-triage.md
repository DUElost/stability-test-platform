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
| **L0 意图 / 作业层（先问这层）** | `USB n` 与在线数一起归零、`lsusb` 只剩 root hub，**但该 host 近一段时间没有 job** | **平台侧读不到判据**——`device` 只有 `ONLINE/OFFLINE/BUSY/ERROR`，没有 retired/archived/空置位 ⇒ 只能问人、看作业与搬迁记录 | 先确认是不是**有意**清空/关机/搬迁（#3065 现网实例）；确认后**不要**继续按 L1–L4 定成因。解除只能来自账侧或规则豁免（#2962 设备意图位、#3068 paging 取舍） |
| **L1 内核 / USB 子系统** | `lsusb`/sysfs 里手机整体变少或为空，`USB n` 同时掉 | `sudo dmesg -T` 出现 `xHCI host not responding` / `HC died; cleaning up` / `error -71(-110)` | xHCI driver unbind/rebind 或 reboot；完整判据见 [8.87 事故复盘](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md)。bind 后 probe 报 `-110`、`enable` 为 0 且驱动目录无 BDF 链接时主控未初始化，rebind 无效，直接 reboot（§6.1）。主控救回后单个 Hub 的 SuperSpeed 分支仍反复枚举失败、或只按 §5.1 的半枚举形态成立时，同样按 §5 处置 |
| **L2 主机 adb server** | 设备在 USB 上**有 ADB 接口**，`adb devices` 却少或为空 | 「ADB 接口设备集合 ⊋ adb 列表集合」（§2 probe）；`pgrep -af 'fork-server server'` 出现多实例 / 非 5037 端口 / 非 Agent 属主 | 多 server（#160）由 Agent 自检并可选自愈；**单 server 卡死**才用 `adb kill-server && adb start-server`（打断在途 adb 会话，须先确认无在跑任务） |
| **L3 设备 adbd / 授权** | `adb devices` 能列出该设备，但 state 是 `offline` / `unauthorized` | 两侧集合相等（无 L2 漏项），且存在非 `device` 行 | `adb reconnect offline`、重插、设备侧确认授权或重启；属**设备侧**，server 重启无效 |
| **L4 设备 USB 功能集** | `USB n > 0` 而在线为 0 或明显偏低 | sysfs 接口里**没有** `ff:42`；`.../1.0/interface` 返回 `MIDI function`（`0e8d:2046`） | 只能设备侧人工：屏幕上把 USB 配置切回 MTP/文件传输并开启 USB 调试；adb 侧一切操作无效（平台侧自 #3046 起给 reason `adb_interfaces_missing`，见 §3） |

PID/接口名是 MLD-LX3 等 MediaTek 机型 2026-09-14 实测口径：`0e8d:201c` 暴露
`ff:42` 且 iInterface=`ADB Interface`（adb 可见，正常态）；`0e8d:2046` 暴露
`01:01`（`MIDI function`）+`01:03`，**没有 ADB 接口，任何 adb server 都看不到**。
注意 `backend/agent/scripts/flash_firmware/*` 注释把 2046 称作「普通态手机」——那里的
含义是「非刷机态」，不代表有 ADB 接口。**关机态设备同样以 `0e8d:2046` 出现在 `lsusb`
里**：此时没有 ADB 接口是预期，`adb_interfaces_missing` 不构成 L4 待办。执行者必须先按
L0 确认这批设备是否处于关机/空置，再决定是否要人到设备侧操作（2026-11-12 `.102`
现网实例，见 §5）。

> **为什么 L0 排在 L1 前面（2026-09-22 现网教训，#3065）**：同一条「整机 USB 看不见」的
> 观察，这天先被读成「控制器故障」（xHCI rebind 被执行，事后确认不必要），再被读成「机柜侧共享
> 成因事件」，而现场真值是**人为把 20 台手机手动断开**、另有两台机柜**设备处于关机**。三次都是
> 同一个推理错：只在「测试断电」与「硬件故障」之间做排除，**从没把「有人正在有意操作这批机器」
> 列进候选**。缺 L0 这层表达能力时，任何自动成因判据都会周期性地把正常作业读成故障——
> `StabilityHostUsbBlind` 会持续 firing 直到账侧改对，就是这个缺口的告警面症状。
> 所以：先问 L0，再跑 §2 的只读采集；两者都排除后才谈 L1–L4 的处置。

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

- `USB n` 徽标（`capacity.usb_device_count`，`frontend/src/components/network/ExpandableHostTable.tsx`）——L4 曾长期是页面上**唯一**可见的信号，#3046 起不再是（下方有 reason）；
- `adb_multiple_servers`（warning 级 reason → DEGRADED，`backend/agent/capacity_reporter.py::_compute_health`），配套自愈 `ensure_single_adb_server()`（`backend/agent/device_discovery.py::ensure_single_adb_server`，需 `STP_ADB_AUTO_REPAIR=1` 且无在跑任务）；
- **L1 的两个内核判据已落地（#2900）**：`usb_host_controller_dead`（内核报 `HC died` / `xHCI … not responding` **且此刻 USB 一台都看不到** ⇒ DEGRADED；设备回树自动回落）与 `usb_link_degraded`（窗口内 `error -71/-110` 或「cable is bad」超阈）。实现：解析与词表在 `backend/agent/contracts/kernel_usb_faults.py`（两侧共用），采集在 `backend/agent/kernel_usb_faults.py`（低频读 `journalctl -k`；**首扫读最近 1 小时**，#2957 起不再读整段 boot——实测某主机 `--boot` 373,600 行，截断样本会冒充完整结果；读不到按「未知」不报）。
  ⚠ **这两条的前提是 agent 能读到内核日志**（现网实测：不满足）。Agent 服务 `User=android`（`backend/agent/stability-test-agent.service:9` 与 `install_agent.sh` 两条路径同写死），安装脚本只加过 `dialout`，从未加 `adm`/`systemd-journal`；`kernel.dmesg_restrict=1` 又堵死 dmesg//`dev/kmsg` 备用路（实测非特权 `open()` 报 `EPERM`）。
  **失效形状（#2957，2026-09-21 由本仓自己的指标证实，不再是推断）**：本模块使用的 argv `journalctl -k --no-pager -o cat --boot` 在非特权下返回 **rc=0 / stdout 空 / stderr 空**——**连权限提示都不打印**（提示只出现在 `-n 3`、`--since -1h` 之类的别的形状里），所以「未知 ≠ 干净」那道守卫在这条路径上不生效，36/36 台已升级 host 一度上报 `usb_kernel_log=ok`（同机 `sudo` 对照：真读到时是 373,600 行）。现在的判据是**正向可读性探针**（`--boot --lines=1` 取不到一行即判未知），并把子进程钉 `LC_ALL=C`/`LANG=C`（systemd 提示串是翻译过的，按文本匹配不能依赖语言环境）。
  ⇒ **读权限已由 #2957 裁决 C 收口**（ADR-0037 v0.6 D7）：wrapper 新增只读子命令
  `stp-agent-priv read-kernel-log`（`--boot|--since-epoch` + `--lines`，固定 argv、环境清空、
  30s 超时、8 MiB 上限，截断即非零退出），Agent 在 `capabilities` 含该子命令时经
  `sudo -n` 调用。**它是逐台 `update_agent.yml` 下发的**（不随热更新）：未更新的主机维持
  「不可用」，`capacity.usb_kernel_log` 仍报 `unavailable`，与现状一致。灰度 1 → 5 → 全量，
  判据 = 该 gauge 从 `unavailable` 回落 + fleet 级告警 `StabilityUsbKernelLogChannelDark`
  回落。**在主机完成 wrapper 更新之前，L1 的人肉判据仍是上机
  `journalctl -k | grep -E 'xhci|HC died'`（§2）**——注意先 `sudo`，非特权下你会拿到同一个空结果。
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
- **#3046 起：L4 不再静默**——`_adb_interfaces_missing`（`usb_device_count > 0 ∧ adb_interface_count == 0`，`None` = 采集失败视为未知、不触发）产出 reason `adb_interfaces_missing`（warning → DEGRADED，不打闸）。它的用途只有一个：**把运维从「反射式重启 adb server」上拉开**——看到这条直接照 §1 L4 行走设备侧人工，§4 的 kill/start-server 对照实验已证 adb 侧无效。
- **#3046 Revisit / #3068：L4 paging 已建**——规则 `StabilityHostAdbInterfacesMissing`
  （warning，`for: 30m`）= `adb_interfaces_missing`。**不合物取设备账**（`usb > 0`
  本身即「本来有设备」证据）；**不做恢复自动化**（报单人撤回方向③）。`for: 30m`
  取 #3068 选型 2（慢性待办、现网常驻命中；短 for 上线即常亮）。处置注解钉死
  §1 L4 设备侧人工，并禁 `adb kill-server` / xHCI rebind。若改走选型 1（仅工单面、不 paging），撤本规则即可。
- **#2967 起：`usb_tree_empty` 的失明 paging 已建**——规则
  `StabilityHostUsbBlind`（critical，`for: 15m`）= `usb_tree_empty` ∧
  `sum(host_device_adb_state) > 0`（该 host `device` 账上有行）。合取两道守卫各有实测依据：
  无合取则空柜机常亮（09-20 命中 tree_empty 的 .91 设备零落账）；无 `for` 则热更重启的
  单 tick 瞬态空树误报（09-21 批量推送中 .61 账上 20 台仍亮过一次）。ghost 行在本合取下
  **反转**为「账实不符该上报」，锚点不设新鲜度窗（防 .63 型 11 天失明第 7 天自静默）。
  它是 #2957 内核通道僵局期间 L1 失明的现役 paging 通道。

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

## 5. L1 恢复后：单个 Hub 的 USB3 分支失效与 USB2 上行旁路

> 警告：USB2 上行旁路是**绕过**故障的 SuperSpeed 分支，不是修复。代价是该 Hub 下设备
> 永久失去 USB3 带宽。执行者只在「下游设备本来就以 USB2 高速枚举」的机柜（刷机 / 测试
> 站）上采用本处置。

主控 rebind 救回整树后，仍可能有单个 Hub 的 USB3 分支没有恢复。两者是不同故障面：
主控死亡时整棵树消失；分支失效时 USB2 伴生树（`1-x`）与下游设备正常，只有 SuperSpeed
树（`2-x`）反复枚举失败。
失效也会以另外两种形态出现，两者都不带重枚举风暴，见判据 1 与 §5.5：SuperSpeed 树停在
半枚举状态（某一级 Hub 无驱动绑定），以及方向相反——USB2 伴生树整棵消失、SuperSpeed 树
留在原地不动。

### 5.1 只读判据

同时满足以下三条时按本节处置：

1. `sudo dmesg -T` 里**同一个 `Bus.Port`**（如 `2-5`）反复出现
   `new SuperSpeed USB device number=...`，随后 `USB disconnect`、
   `Cannot enable. Maybe the USB cable is bad?`、`device descriptor read/64, error -32`
   或 `config failed, error -22`；
   执行者也可以按**半枚举形态**成立本条：该分支没有任何重枚举记录，但 `lsusb -t` 里某一级
   Hub 显示 `Driver=[none]/0p`（设备号已分配、hub 驱动始终没绑上），且内核持续打印
   `hub <Bus.Port>:1.0: hub_ext_port_status failed (err = -110)`。查法：`lsusb -t`，以及
   只读的 `sudo -n /usr/local/sbin/stp-agent-priv read-kernel-log --since-epoch <now-900>
   --lines 5000`（ADR-0037 D7；`kernel.dmesg_restrict=1` 下非特权 `dmesg` 读不到）。
   执行者不得因为缺 `new SuperSpeed USB device` 而排除本条。
2. `uhubctl` 里该 SuperSpeed Hub 的端口状态含 `oc`（过流），或该 Hub 从 `uhubctl`
   列表中消失而内核仍在重试；其 USB2 伴生 Hub（如 `1-5`）与下游设备持续枚举；
3. `lsusb -t` 的 Bus 002（SuperSpeed root hub）下没有其他可用 hub 子树。

平台侧 `usb_link_degraded`（§3）只说明窗口内有 `-71/-110` 或 cable-suspect 计数超阈，
不区分「主控级」与「单分支级」；分支级判据必须靠上面三条人工取证。

`-110` 计数本身也不是判据：多数主机的 `-71/-110` 来自 `Failed to suspend device`
（autosuspend 失败），对象仍在树上、`lsusb` 不丢。执行者看到 `usb_link_degraded` 时，必须
先判定发噪声的**对象是 Hub 还是终端设备**：内核的 `usb <X>: Failed to suspend device` 这行
Hub 同样会打印，执行者不得据此认定是手机侧。对象判定的查法：
`cat /sys/bus/usb/devices/<X>/bDeviceClass`，输出 `09` 即 Hub。
故障对象是 Hub 时，判据 1 的两种形态任一成立即按本节处置。执行者只有在确认对象是终端设备、
且判据 1 与判据 2 都不成立时，才不得按本节处置（2026-11-18 一台主机正是被这条误判放过，
复盘见 §5.5）。

### 5.2 处置步骤

1. 执行者确认 `lsusb -t` 里所有下游设备都挂在 USB2 树下、Bus 002 无设备——SuperSpeed
   分支失效后，这些设备本来也只能以 USB2 高速枚举。
2. 执行者确认该 Hub 有独立电源。USB2 端口供电预算 500 mA、USB3 为 900 mA，多级级联
   Hub 在 USB2 下更容易触碰过流。
3. 执行者把 Hub 上行线从 USB3 口（蓝色 / SS）改插**纯 USB2 口**（黑色），或改用
   USB2-only 上行线。执行者不得改插同一主机的另一个 USB3 口——过流 / 链路错误随 Hub
   侧 SS PHY 走，换口不改变故障面。
   旁路只搬走了在用的那根上行；失效分支的上行线继续插着时，它会留在设备列表里继续污染
   `uhubctl`。执行者必须在旁路后按 §5.4 把残留上行摘掉（现场拔线，或软件 `remove`）。
4. 执行者验证切换结果：`lsusb -t` 中 Bus 002 下无 hub 子树；`1-5` 及其下游 `1-5.x`
   全部重新枚举；`lsusb` 设备总数与切换前一致；60 秒内 `dmesg -T` 无新的 `2-5` 枚举
   失败或 `-71`。
5. 执行者核对平台信号：下一次心跳 `capacity.usb_device_count` 恢复；`usb_link_degraded`
   在 1 小时统计窗滑过后回落。
6. 执行者排期更换该 Hub。USB2 旁路是过渡处置，不是修复；这类 Hub 是单一整机，内部级联
   结构不可拆分或改造，执行者只能选「换上行口」或「整只更换」。

### 5.3 2026-11-12 `.102` 实例

2026-11-10 16:37 该主机 xHCI 主控死亡（`HC died; cleaning up`），11-12 rebind 后整树
恢复。此后 `2-5`（`0bda:0411` USB3.2 Hub）持续出现 `uhubctl` 过流标记
（`fffffffc ... oc`）与反复枚举重试，最终从 `uhubctl` 消失而内核仍在重试；USB2 伴生
`1-5`（`0bda:5411`）与 15 台关机态 `0e8d:2046` 始终稳定。

改插纯 USB2 口后：Bus 002 无 hub 子树；15 台设备 50 秒内无断开、无 `-71`；平台
`usb_device_count` 从切换瞬态的 5 回到 15；`health.reasons` 只剩
`adb_interfaces_missing`——这是关机态的预期结果（§1 L4 后的说明），不需要设备侧操作。

该分支劣化有长期前兆：这台主机死亡前两周每日 `-71/-110` 达 400~3700 行（阈值来源见
`backend/agent/contracts/kernel_usb_faults.py` 注释）。

2026-11-12 舰队核对：另外三台报 `usb_link_degraded` 的主机（`.42`、`.60`、`.75`）的
`-71/-110` 全部来自单个设备的 `Failed to suspend device`，`lsusb` 设备数正常、24 小时内
零 `USB disconnect`，因此不属本节形态、不做处置。`.54` 属同形：`2-5` 持续
`device descriptor read/8, error -110`（24 小时 12,313 次重新枚举），USB2 侧
`1-5` 与其内部五个 `0bda:5411` 分级正常，Bus 002 只有 root hub。

**2026-11-18 勘误**：上述核对对 `.75` 的结论是误判。发 `-110` 的对象不是终端设备，而是该
主机 SuperSpeed 分支最深一级的 Hub（`usb 2-5.4: Failed to suspend device` 与
`hub 2-5:1.0: hub_ext_port_status failed` 同时出现，且 `2-5.4` 自 2026-10-21 起就处于
`Driver=[none]/0p`）。误判的成因是把 `Failed to suspend device` 当成了「设备侧」签名——
见 §5.1 的对象判定；取证与处置见 §5.5。`.42`、`.60` 本次未复核，结论保持不变。

2026-11-12 11:41 `.54` 按本节处置完成：上行改插纯 USB2 口（Bus 001 端口 9）后，
`2-5` 事件归零，`1-9` 及内部 `1-9.1..4` 与 13 台设备恢复，连续 3 分钟零重枚举，
平台 `usb_device_count` 从切换瞬态的 0 回到 13。该机设备为 TECNO
（`0e8d:2008` / `0e8d:20ff`），只暴露 MTP 接口、无 `ff:42`，`adb devices` 为空属
§1 L4 设备侧，与本次旁路无关。

### 5.4 摘掉残留上行：`uhubctl` 固定超时的解法

适用条件：一条已失效的 Hub 上行分支仍插在主机上（§5.1 判据 1 的半枚举形态，或 §5.2 旁路后的
残留上行），而现场需要用 `uhubctl` 做端口级取证。

`uhubctl` 无论 `-l` / `-p` 把范围收得多小，都会打开并查询系统里**所有** Hub 才能解析位置；
查到失效分支时每次都吃满内核控制超时。实测形态：单口定向调用与全量列举同样慢，耗时与请求的
端口数无关。执行者不得把这段耗时当成「整机 USB 有问题」或「在用 Hub 也坏了」——纯 sysfs 读取
（`lsusb`、`lsusb -t`、`/sys/bus/usb/devices/*`）不受它影响，实测差三个数量级。
该开销同样落在 `stp-agent-priv usb-power` 的调用路径上：其超时上限 60 秒
（`backend/agent/stp_agent_priv.py` 的 `USB_POWER_TIMEOUT_SECONDS`，ADR-0037 D8），
而 `cycle` 的 `delay` 最多再加 30 秒（`USB_POWER_MAX_DELAY`），余量会被这段固定开销吃掉。

主机未安装 `uhubctl` 时（实测：`.42` 的 `/usr/sbin/uhubctl` 不存在），执行者**不得**把
「`uhubctl` 不慢」当成「本机没有本节形态」的反证——该结论没有观测基础。此时判据 1 的半枚举
形态与处置验收一律改用零权限观测面：`ls /sys/bus/usb/devices/<Bus.Port>:1.0/driver`（无链接即未绑定）、
`lsusb -t` 的 `Driver=[none]/0p`、`lsusb` 设备总数、`adb devices` 串号集合，以及
`stp-agent-priv read-kernel-log` 的计数（见 §5.6 `.42` 实例）。

定位「噪声何时停止」时，执行者必须用 `--since-epoch` **窗口夹逼**：`read-kernel-log` 走
`journalctl -k -o cat`，输出**不含时间戳**，因此无法直接读出末条事件的时刻。查法：按递降窗口
（如 3/5/10/15/20 分钟）分别统计 `grep -c 'error -110'`，取「计数首次归零的最小窗口」，
再与本次拆除时刻对齐核验。

> 警告：第 3 步是 root 写操作，且 sysfs 的 `remove` 是 **write-once、软件侧不可撤销**。
> 写下去之后该子树只能靠现场重新插线、或 rebind xHCI 驱动恢复；后者会把同一控制器上
> **另一条总线正在使用的设备一起重新枚举**。执行者必须先拿到 Owner 对本主机本次操作的授权，
> 并按 §1 L0 确认该主机无在跑作业。

按顺序执行以下步骤：

1. 确认失效子树不承载任何在用设备：`lsusb -t`，逐行看该 Bus 子树下有无非 Hub 设备。
   预期：非 Hub 设备数为 0。不为 0 时停止本流程，改走现场处置。
2. 确认失效分支的顶层对象是 root hub 的直接子设备：
   `basename $(readlink -f /sys/bus/usb/devices/<Bus.Port>)`（例如 `2-5` → `usb2`）。
   预期：输出形如 `usb1` / `usb2`。这一步决定第 3 步的 port-disable 请求是发给 root hub
   还是发给那只已经不响应的 Hub；是后者时 `remove` 自己也会卡在超时里，执行者必须改走现场拔线。
3. 对顶层对象执行软件断开：`echo 1 | sudo tee /sys/bus/usb/devices/<Bus.Port>/remove`。
   预期：命令可阻塞十几到几十秒（内核要先耗掉在途控制请求）；返回后
   `ls /sys/bus/usb/devices/ | grep -E '^<Bus>-'` 只剩 root hub 自身的接口目录；
   `lsusb` 设备总数减少量等于被摘掉的 Hub 只数。
4. 复测取证工具：`time uhubctl`，再做一次定向
   `time uhubctl -l <在用 Hub 位置> -p <口> -a status -d 1`。
   预期：全量列举降到秒级、定向调用降到亚秒级；在用侧列出的设备串号集合与第 3 步前逐条一致。
5. 核对平台信号：下一次心跳 `capacity.usb_device_count` 与在用设备数一致；
   `usb_link_degraded` 在 1 小时统计窗滑过后回落（`contracts/kernel_usb_faults.py` 的
   `LINK_WINDOW_SECONDS`）。
6. 排期更换该 Hub。`remove` 只停止软件面污染，不修硬件；终态出口仍是 §5.2 第 6 条。
   以后要重新启用这条分支时，执行者先换 Hub 或换上行线，再重插并复跑 §5.2 第 4 条与本节第 4 步。

### 5.5 2026-11-18 `.75` 实例（半枚举形态与 `uhubctl` 固定超时）

HONOR MLD-NX2 站，16 台设备，xHCI 为单一控制器（两条总线：480M 与 5000M），开机 95 天未重启。

- 取证（全程只读）：`uhubctl` 各形态耗时 22~31s，而 `user+sys` 合计 0.02~0.06s；阻塞时
  `state=D`、`wchan=usb_start_wait_urb`，当时的 fd 指向失效分支最深一级 Hub 的
  `/dev/bus/usb` 节点。同机 `lsusb` 0.011s。单口定向调用（含 `-d 1`）26.8s——耗时与端口数无关。
- 拓扑：在用侧挂在一条总线的根口号 10 上，`0bda:5411` 五只级联、20 个口全部
  `power highspeed enable connect`、无 `oc`；失效侧在另一条总线的根口号 5 上，`0bda:0411`
  五只级联、子树下设备数 0、最深一级自 2026-10-21 起 `Driver=[none]/0p`。每条总线各只有一个
  顶级设备对象 ⇒ 两侧是两根独立上行线，这是本次能安全 `remove` 的前提；执行者不得把它推广到
  单根 USB3 线的场合。
- 判据成立形态：15 分钟内核窗内 `hub_ext_port_status failed (err = -110)` 67 次、
  `usb <X>: Failed to suspend device, error -110` 68 次，两者对象都是 Hub（`bDeviceClass=09`）；
  同窗在用侧 0 条、重枚举 0、`USB disconnect` 0。判据 1 的重枚举风暴形态不成立、半枚举形态成立。
- 处置与结果：按 §5.4 摘掉失效侧顶层对象（写入耗时 20.6s）。之后 `uhubctl` 全量 1.30s、
  定向 0.30s；`lsusb` 28→23；在用侧 16 台串号逐条一致、`adb` 口径未变；内核不再出现失效侧任何行。
- 同期发现的配置漂移：当日 11:30 Ansible 写入的 VBUS 槽位映射仍指向改插前的顶级口号，
  19:47:37 该树已改插到新根口号（sysfs 目录 mtime 为证），11 条映射口的 sysfs 目录全部不存在
  → `usb-power` 在查表阶段即 fail-closed。逐条比对结果：11/11 只有顶级口号变化，子 Hub 与端口号
  不变。重登记与 5 台无槽位设备的缺口见
  [`notes/process/2026-11-18-host-15-75-ss-wedge-uhubctl-timeout.md`](../notes/process/2026-11-18-host-15-75-ss-wedge-uhubctl-timeout.md)。

### 5.6 2026-11-19 `.60` 与 `.42` 实例（同形复现，验收含 reason 翻转）

§5.5 的形态在另外两台主机上复现并经软件摘除闭环。两台都是 `0bda:0411` 五只级联、
**最深一只 Hub 始终未绑定 hub 驱动**（`Driver=[none]/0p`）、**该子树承载 0 台在用设备**，
在用侧全部落在另一根总线的另一根上行线上。

`.60`（13 台 HONOR MLD-NX2，`uhubctl` 已安装）：

- 处置前（14:48:21）：`uhubctl` 全量 **21.8s**；**单口定向** `-l 1-4.4 -p 1 -a status -d 1`
  **27.3s**（比全量列举还慢 ⇒ 「耗时与请求端口数无关」的直接证据）；15 分钟内核窗
  `error -110`=73、`hub_ext_port_status failed`=72、`Failed to suspend device`=73，
  对象均为 Hub；**在用侧 `usb 1-x` 0 条**、重枚举 0；`lsusb` 设备 25；bus 2 非 Hub 设备 0；
  `2-3` 的 parent = `usb2`（root hub）；`usb_power_slots=0`（该机无 VBUS 切电映射）；
  `active_jobs=0`、`held=0`。
- 处置：`echo 1 > /sys/bus/usb/devices/2-3/remove` → `rc=0`，**写入阻塞 19.5 秒**。
- 处置后（14:50–14:56）：全量 **0.9s → 0.6s**、定向 **0.3s**；bus 2 仅剩 `2-0:1.0`；
  `lsusb` 25 → **20**（= 被摘掉的 5 只 Hub）；**13 台设备串号集合逐条 diff 一致**、
  `adb` 在线 13 → 13；`/mnt/hdd` avail 未变；窗口夹逼 3/5/10/15/20 分钟 =
  **0 / 0 / 19 / 44 / 68** ⇒ 噪声停止时刻即拆除时刻 14:50；日志末两行为拆除收尾
  （`Disable of device-initiated U2 failed` 与 `usb 2-3: USB disconnect, device number 7`）。
- 平台验收：`usb_link_degraded` 于 **15:47:02** 消失，主机转 **HEALTHY**（距拆除 57 分钟，
  与 `contracts/kernel_usb_faults.py` 的 `LINK_WINDOW_SECONDS=3600` 一致）。

`.42`（17 台设备，`adb` 全在线；**该机未安装 `uhubctl`**）：

- 判据与验收全部改走零权限观测面（见 §5.4 新增口径）：处置前（17:27:44）`lsusb` 设备 29、
  bus 2 顶级对象仅 `2-3`（5 只 Hub、非 Hub 设备 0）、`2-3.4` 无驱动链接、parent = `usb2`、
  `usb_power_slots=0`、`active_jobs=0`、`held=0`；15 分钟内核窗 `error -110`=74、
  `hub_ext_port_status failed`=73、对象唯一为 `usb 2-3.4`，**在用侧 0 条**、重枚举 0。
- 处置：`echo 1 > /sys/bus/usb/devices/2-3/remove` → `rc=0`，阻塞 15.9 秒。
- 处置后：bus 2 仅剩 `2-0:1.0`；`lsusb` 29 → **24**；**17 条 `adb` 串号逐条 diff 一致**；
  `/mnt/hdd` avail 未变；窗口夹逼（17:33:32）2/5/10 分钟 = **0 / 1 / 26**
  ⇒ 噪声停止时刻即拆除时刻 17:28:55。
- 结论：`uhubctl` 缺席不影响本节判定，也不影响摘除带来的收益（内核 `-110` 归零、
  健康面 reason 随滑窗回落）。
  平台侧 `usb_link_degraded` 的翻转按 `LINK_WINDOW_SECONDS=3600` 推算约为 18:29；
  **本文撰写时（17:40）该机 reason 仍未翻转，故 `.42` 的平台验收记为未完成**，不得当已完成引用。

三例同形说明这不是个案，而是同一批 Hub 的 SuperSpeed 侧退化。`remove` 只停止软件面污染，
执行者仍须按 §5.2 第 6 条排期更换这些 Hub；`remove` 前不得省略 §5.4 第 1、2 步前置。

## 6. 相关

- 列的设计与口径：[`notes/feature/2026-09-11-hosts-usb-device-count.md`](../notes/feature/2026-09-11-hosts-usb-device-count.md)
- 只读诊断边界与凭据来源：[`production-diagnostics.md`](./production-diagnostics.md)
- xHCI 死亡致 USB/ADB 全空：[`incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md`](./incident-2026-07-29-host-8-87-xhci-death-and-adb-outage.md)
- 半枚举形态与 `remove` 解法的决策记录：[`notes/process/2026-11-18-host-15-75-ss-wedge-uhubctl-timeout.md`](../notes/process/2026-11-18-host-15-75-ss-wedge-uhubctl-timeout.md)
- 观测口径补强与 `.60`/`.42` 两例处置的决策记录：[`notes/process/2026-11-19-usb-hub-half-enum-second-hosts-skill-guard.md`](../notes/process/2026-11-19-usb-hub-half-enum-second-hosts-skill-guard.md)
- HONOR / MLD 刷机 runbook：[`honor-flash-runbook.md`](./honor-flash-runbook.md)
