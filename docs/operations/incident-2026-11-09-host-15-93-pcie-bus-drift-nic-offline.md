# 2026-11-09 host .93 PCIe 总线漂移致板载网卡重命名（enp2s0 ↔ enp3s0）与物理灭灯失联复盘

> **状态**：已现场固化修复并完成双重容错（2026-11-09 23:44 写入 `/etc/systemd/network/10-persistent-net.link` 强制按 MAC 固化为 `enp2s0`，并配置 `/etc/network/interfaces.d/enp3s0` 静态回退，重刷 initramfs 验证通过）。  
> **影响范围**：单台 Agent host（`172.21.x.93`，Dell OptiPlex 3090，主板 0492YX，BIOS 2.28.0）。在软重启或特定开机状态下，主板 CPU PCIe x16 根端口（`00:01.0`）动态显隐，导致板载 Realtek RTL8168 网卡在 PCIe 总线上的物理编号在 `0000:02:00.0` 与 `0000:03:00.0` 之间跳变。当跳变至 `enp3s0` 时，Debian 静态网络服务无法匹配，网卡保持行政关闭（DOWN）态；Realtek 驱动因此将物理 PHY 置于休眠，导致 RJ45 网口物理指示灯完全不亮、无 IP、整机网络彻底失联。  
> **关联**：[#3749](https://github.com/DUElost/stability-test-platform/issues/3749)（host .93 USB 链路劣化先例）、[#3805](https://github.com/DUElost/stability-test-platform/issues/3805)（主机环境与全网普查）、[`host-device-visibility-triage.md`](./host-device-visibility-triage.md)。

---

## 1. 现象与时间线

排查失联主机 `172.21.x.93` 时，现场反映：**“插上网线后网口指示灯完全不亮，显示器接控制台执行 `ip a` 显示网卡为 DOWN，无 IP 分配；拔插网线或重启多次后偶现恢复正常”**。

通过分析该主机当天的多轮启动记录（`journalctl --list-boots`）与内核日志，梳理出完整时间线：

| Boot 序号 | 时刻（host CST, UTC+8） | 网卡总线与系统识别名 | 网络配置与链路状态 | 网口物理指示灯表现 |
| :---: | :--- | :---: | :---: | :---: |
| **Boot -14** | 2026-08-28 13:39 ~ 11-08 22:48 | `0000:02:00.0` (`enp2s0`) | 正常在线约 72 天，静态 IP 正常 | 正常点亮（1Gbps 绿灯） |
| **Boot -13** | 2026-11-09 10:46 ~ 18:20 | `0000:03:00.0` (**`enp3s0`**) | **无 IP，无链路，失联约 7.5 小时** | **物理灭灯**（误以为断线） |
| **Boot -12** | 2026-11-09 18:21 ~ 18:24 | `0000:03:00.0` (**`enp3s0`**) | 无 IP，未拉起，失联 | **物理灭灯** |
| **Boot -11** | 2026-11-09 18:25 ~ 19:19 | `0000:03:00.0` (**`enp3s0`**) | 无 IP，未拉起，失联 | **物理灭灯** |
| **Boot -10** | 2026-11-09 19:27 ~ 19:33 | `0000:02:00.0` (**`enp2s0`**) | 自动拉起，获取静态 IP，Link Up (1Gbps) | 正常点亮 |
| **Boot -9** | 2026-11-09 19:33 ~ 19:36 | `0000:02:00.0` (**`enp2s0`**) | 自动拉起，获取静态 IP，Link Up (1Gbps) | 正常点亮 |
| **Boot -8 ~ -6** | 2026-11-09 19:36 ~ 19:48 | `0000:03:00.0` (**`enp3s0`**) | 无 IP，未拉起，失联 | **物理灭灯** |
| **Boot -5 ~ -4** | 2026-11-09 19:49 ~ 19:54 | `0000:02:00.0` (**`enp2s0`**) | 自动拉起，获取静态 IP，Link Up (1Gbps) | 正常点亮 |
| **Boot -3 ~ -1** | 2026-11-09 19:56 ~ 20:33 | `0000:03:00.0` (**`enp3s0`**) | 无 IP，未拉起，失联 | **物理灭灯** |
| **Boot 0 (当前)** | 2026-11-09 20:34 至今 | `0000:02:00.0` (**`enp2s0`**) | 自动拉起，获取静态 IP，恢复正常 | 正常点亮 |

**规律呈现 100% 对应**：
- 只要系统将网卡识别为 **`enp2s0`**，网络立刻 Link Up，静态 IP 自动生效，物理指示灯正常点亮；
- 只要系统将网卡识别为 **`enp3s0`**，接口保持 DOWN，指示灯绝对不亮，网络彻底失联。

---

## 2. 根因技术判定（四层因果链）

### 第 1 层：Dell BIOS PCIe 根端口显隐导致总线重编号（Bus Renumbering）
* **机型特征**：Dell OptiPlex 3090 小型台式机（Comet Lake Q470 芯片组）。
* **总线对比**：
  * **在网卡为 `enp3s0` 的启动中（如 Boot -1）**：
    BIOS 向上申报了空置的 CPU PCIe x16 根端口 `0000:00:01.0` [8086:1901]（PCI bridge to [bus 01]）。该端口占用了 Bus 01，导致 M.2 NVMe SSD 顺延为 Bus 02（`0000:02:00.0`），板载 Realtek RTL8168 网卡顺延为 **Bus 03（`0000:03:00.0`）**。
  * **在网卡为 `enp2s0` 的启动中（如 Boot 0）**：
    BIOS 未向操作系统暴露 `0000:00:01.0` 桥。M.2 NVMe SSD 占用 Bus 01，板载网卡占用 **Bus 02（`0000:02:00.0`）**。
* **显隐根因**：Dell UEFI 固件在软重启（Warm Reboot）与断电冷启动（Cold Boot）下，对未插卡的 CPU PEG PCIe 根端口链路训练/电源门控策略存在状态差异，引发总线编号偶发性跳变。

### 第 2 层：systemd Predictable Network Interface Names 重命名机制
Linux systemd 默认根据 PCI 物理拓扑路径生成接口名（`ID_NET_NAME_PATH=enp<bus>s<slot>`）：
* 网卡在 Bus 02 时：生成名称 `enp2s0`；
* 网卡在 Bus 03 时：生成名称 `enp3s0`。

### 第 3 层：Debian 静态网络配置单点写死
主机 `/etc/network/interfaces` 静态配置如下：
```text
allow-hotplug enp2s0
iface enp2s0 inet static
    address 172.21.x.93/23
    gateway 172.21.x.254
```
由于配置仅匹配 `enp2s0`，当网卡名为 `enp3s0` 时，Debian 的 `ifupdown`（`networking.service`）完全不会去配置或启动 `enp3s0`，导致网卡处于未激活状态（Administrative DOWN）。

### 第 4 层：Realtek r8169 驱动的 PHY Powerdown 机制引发物理灭灯
为什么插上网线后连物理链路灯（Link LED）都不亮？
* 传统认识往往认为“只要物理线通电接上交换机，无论系统如何，网口硬件灯一定会亮”；
* 但对于 Realtek RTL8168/8111 系列网卡，内核驱动 `r8169` 默认开启了 **PHY 节能电源管理（PHY Powerdown when interface is down）**；
* 当操作系统未执行 `ip link set <dev> up` 将网络设备激活时，驱动会向 PHY 寄存器发送下电指令关闭 PHY 发射模块；
* **处于 PHY Powerdown 状态的网卡无法与对端交换机完成物理脉冲握手（NLP/FLP），因此 RJ45 端口的双色指示灯保持彻底熄灭**。这造成了极其逼真的“硬件或网线损坏”假象。

---

## 3. 排除清单（非物理故障）

| 怀疑点 | 排除依据 |
|---|---|
| **网线损坏 / 水晶头松动** | 排除。在多次重启中，完全相同的网线在 `enp2s0` 状态下能以 1Gbps/Full 正常通信、双向 ping 0% 丢包，未触碰网线即可恢复。 |
| **交换机端口故障 / err-disable** | 排除。对端端口工作正常，无端口震荡惩罚（flap dampening）或 VLAN 异常。 |
| **网卡芯片硬件损坏** | 排除。网卡 PCI 配置空间始终可读，MAC 地址始终有效，一旦激活即可全速通信。 |

---

## 4. 实施修复与长效加固（双重容错 SOP）

2026-11-09 23:44 已在 `172.21.x.93` 实施并验证了双重容错防护策略：

### 步骤 1：按 MAC 地址固化 systemd Link 规则（主防御）
创建 `/etc/systemd/network/10-persistent-net.link`（权限 `0644 root:root`）：
```ini
[Match]
MACAddress=c0:25:a5:d1:5f:87

[Link]
Name=enp2s0
```
* **效果**：无论底层 PCIe Bus 是 02 还是 03，systemd-udevd 会强制将其覆盖命名为 `enp2s0`。
* **验证**：执行 `udevadm test-builtin net_setup_link /sys/class/net/enp2s0`，输出已明确断言：
  ```text
  enp2s0: Config file /etc/systemd/network/10-persistent-net.link is applied
  ID_NET_NAME=enp2s0
  ```

### 步骤 2：配置 `enp3s0` 静态网络回退段（兜底防御）
创建 `/etc/network/interfaces.d/enp3s0`（权限 `0644 root:root`）：
```text
# Fallback for enp3s0 in case PCI bus renumbering occurs without udev link rename
allow-hotplug enp3s0
iface enp3s0 inet static
    address 172.21.x.93/23
    gateway 172.21.x.254
    dns-nameserver 172.21.x.2 172.21.x.3
```
* **效果**：即便在极端异常场景下 udev link 规则未加载、网卡依然呈现为 `enp3s0`，Debian 也会自动配置静态 IP 并拉起网卡，确保 PHY 唤醒亮灯并连通网络。

### 步骤 3：更新内核 initramfs
执行 `update-initramfs -u`，将 link 规则同步烧录进 `/boot/initrd.img-6.12.73+deb13-amd64`，确保系统早启阶段具备一致命名。

---

## 5. 硬件排障经验与机队建议

1. **“插线不亮灯 + DOWN” 的第一排查反应**：
   当现场发现主机插上网线不亮灯且系统显示 DOWN 时，**不得盲目断定为网线或网卡损坏**；应第一时间在控制台检查内核网卡重命名记录：
   ```bash
   journalctl -b 0 -k | grep -iE "renamed from eth0|r8169|e1000e"
   ip link show
   ```
   若发现当前网卡名与 `/etc/network/interfaces` 中的名称不一致（如预想是 `enp2s0` 但实际出现 `enp3s0`），可立即通过手工执行 `sudo ip link set <实际名> up` 验证——若执行后网口指示灯瞬间点亮，即确诊为名称不匹配导致的 PHY 休眠。
2. **存量台式机接入规范**：
   Dell OptiPlex 等品牌台式机由于多槽位 PCIe 根端口的显隐特性，容易受软硬件重启状态影响产生总线跳变。建议在后续新机接入或模板镜像中，统一推行基于物理 MAC 地址的 `.link` 命名固化规范。
