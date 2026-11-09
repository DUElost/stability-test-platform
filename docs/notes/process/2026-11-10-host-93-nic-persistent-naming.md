# host .93 板载网卡 PCIe 总线漂移命名固化与双重容错

Status: implemented
Class: process

## Decision

1. 在 `172.21.x.93`（Dell OptiPlex 3090）落地基于物理 MAC 地址（`c0:25:a5:d1:5f:87`）的 systemd link 命名固化规则 `/etc/systemd/network/10-persistent-net.link`，无论主板 CPU PEG 根端口是否在启动时显隐、PCIe 总线编号在 02/03 间如何跳变，网卡名称恒定解析为 `enp2s0`。
2. 同步建立第二重兜底：在 `/etc/network/interfaces.d/enp3s0` 写入同等静态网络配置。即便极端情况下 udev 规则未加载且总线顺延至 03，Debian 网络服务也能自动拉起 `enp3s0`，杜绝因 Administrative DOWN 导致 Realtek RTL8168 PHY Powerdown 休眠灭灯。
3. 执行 `update-initramfs -u`，将命名规则持久化注入内核 initramfs。
4. 归档复盘与排障经验至 `docs/operations/incident-2026-11-09-host-15-93-pcie-bus-drift-nic-offline.md` 并更新 `docs/operations/README.md` 索引。

## Alternatives

- 仅修改 `/etc/network/interfaces` 增加 `allow-hotplug enp3s0`：放弃。虽然能解决网络分配，但网卡名仍会在 `enp2s0` 与 `enp3s0` 间跳变，导致监控指标标签与 Ansible 事实采集分叉。
- 进 BIOS 禁用 PCIe x16 插槽：放弃。Dell BIOS 固件在软重启下仍可能存在 ACPI 桥申报缺陷，纯硬件/BIOS 配置无法由操作系统配置面代码化收口。
- 仅配置 `.link` 文件而不加 `interfaces.d` 兜底：放弃。单点依赖 udev 在特定维护/急救环境存在被绕过风险，双重容错成本为零且更具防御韧性。

## Verification

- `udevadm test-builtin net_setup_link /sys/class/net/enp2s0` 输出断言加载 `10-persistent-net.link` 且 `ID_NET_NAME=enp2s0`。
- `update-initramfs -u` 成功生成新的 initrd 镜像。
- 网关 `172.21.x.254` 双向 ping 测试 0% 丢包（RTT ~1.4ms）。
- 权限审计：所有新增配置文件均为 `0644 root:root`。

## Revisit

- 若后续引入新的 Dell OptiPlex 存量主机接入且出现同类总线跳变现象，考虑在 `install_agent.yml` 或新机接入 SOP 模板中统一内置基于 MAC 的 link 固化规则。
