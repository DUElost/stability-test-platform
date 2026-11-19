---
name: vbus-flash-ops
description: >-
  关机 MTK 设备走 trigger_mode="vbus_cycle" 批量自动化刷机的标准作业与排障 SOP
  （USB 2.0 拓扑核查 → 槽位重刷 → 单口 12s 切电验证 → 每批 ≤ 2 台串行推流 →
  真假断电指纹分诊 → 收尾恢复 0000 off 端口）。触发时机：准备启动 VBUS 刷机、
  Hub 上行口换插黑色 USB 口、批量出现 scan device com port failed / 0e8d:2046
  直出、或刷机结束后清点恢复被隔离断电的 Hub 端口。
---

# VBUS 切电自动化刷机作业与排障 SOP

适用条件：目标设备处于关机无 ADB 态（USB `0e8d:2046`），经可控 USB Hub
走 `flash_firmware@1.4.3+` 的 `trigger_mode="vbus_cycle"` 刷机，或排查其切电异常。
完整背景与扩展细节见 `docs/operations/honor-flash-runbook.md` §10。

> 警告：不得在 `lsusb -t` 显示 `Bus 002` 存在 `0bda:0411`（USB 3.0 Hub）时启动批量
> VBUS 刷机。USB 3.0 口上 `uhubctl` 无法切断物理 5V VBUS，设备不会进入 `0e8d:2000`。

> 警告：物理换接主机 USB 口或拔插设备线缆后，Hub 根路径（如 `1-9` 与 `1-10`）与端口
> 映射会漂移。操作者必须先重新采集 sysfs 串号并下发 `/etc/stp-agent/usb-power-slots.json`，
> 未同步槽位不得直接触发 PlanRun。

## 一、前置核查与槽位同步

按顺序执行以下步骤，不得跳步：

1. 检查 Agent host 的 USB 总线拓扑：
   `lsusb -t`
   预期：`Bus 001` 下存在目标 `Hub` 树，`Bus 002` 下无 `0bda:0411`（`5000M` / `10000M`）。
2. 如果 `Bus 002` 下存在 `0bda:0411`，停止本流程，先让现场将 Hub 上行口改插至黑色 USB 2.0 口。
3. 检查 `uhubctl` 与提权 wrapper 能力：
   `which uhubctl && sudo -n /usr/local/sbin/stp-agent-priv selftest && /usr/local/sbin/stp-agent-priv capabilities`
   预期：输出 `STP_AGENT_PRIV_SELFTEST_OK`，且能力列表同时包含 `usb-power` 与 `usb-power/expect-slot`。
4. 如果第 3 步缺少能力或未装 `uhubctl`，停止触发 PlanRun，先按 `tools/ansible/playbooks/update_agent.yml` 补齐环境。
5. 读取各子 Hub 口当前在线/关机设备的 sysfs 串号并更新槽位变量文件 `<vars.json>`：
   `for d in /sys/bus/usb/devices/1-*.*.*; do [ -e "$d/serial" ] && echo "$(basename "$d") $(cat "$d/idVendor"):$(cat "$d/idProduct") $(cat "$d/serial")"; done`
   预期：在位设备 `serial` 与控制面设备表逐一匹配。
6. 下发槽位文件 `/etc/stp-agent/usb-power-slots.json`：
   `ansible-playbook -i tools/ansible/inventory.ini tools/ansible/playbooks/ensure_usb_power_slots.yml -e usb_power_hosts=<host> -e @<vars.json>`
   预期：`uhubctl_rc=0`、`wrapper_has_usb_power_expect_slot=True`、`slots=<在位台数>`、`failed=0`。
7. 换接黑色 USB 口或首次放量前，挑 1 个槽位做 12 秒切电验证并盯抓内核 USB 事件：
   `sudo -n uhubctl -l <hub_location> -p <port> -a off && sleep 12 && sudo -n uhubctl -l <hub_location> -p <port> -a on`
   `journalctl -k --since "1 minute ago" --no-pager | grep -E "<gate_port>|idProduct|Preloader|ttyACM"`
   预期：`USB disconnect` 后约 `20–21s`（上电后 `8–9s`）出现 `0e8d:2000`（`MT65xx Preloader`）与 `ttyACM`。
8. 如果第 7 步在 `17–18s` 后直接重报 `0e8d:2046` 且充电图标未灭，停止在该黑色口继续批量刷机，按第三节更换另一个黑色 USB 2.0 口。

## 二、放量节奏与控制面护栏

以下各条均为对操作者的要求：

- 同一 Agent host 每次下发的 `vbus_cycle` PlanRun 必须控制在每批不超过 2 台设备，
  且必须在上一批 PlanRun 到达终态后再触发下一批（防主机串行刷机锁导致 15 分钟 `pending_timeout`）。
- 启动批量 VBUS 刷机前，操作者必须在控制面检查负载与磁盘 I/O（`uptime`、`iostat -xz 1 3`），
  不得在重型 I/O 扫描（如 `start_log_scan.py -m`）饱和期间放量（防拖垮 NFS 刷包与 Agent 心跳，见 #4054）。

## 三、失败分型指纹与止损规则

`0e8d:2000` 仅闪现 2–3 秒，操作者建议使用 `journalctl -k` 或 `dmesg -Tw` 复盘，不得仅凭单次 `lsusb` 判断。

| 内核与现场特征 | 根因判定 | 处置规则 |
|---|---|---|
| `USB disconnect` 后 `17–18s` 直出 `0e8d:2046`（`off` 超 45m 或切父口仍 0–1s 回 `2046`），关机亮充电图标 | 物理 5V 未断（主板 USB 口差异）；若拔线按电源键也亮充电图标属手机 PMIC 卡死 | 多口复现时，操作者必须换插主板另一个黑色 USB 2.0 口并重做第一节；拔线仍亮图标时，可以先重启手机后再试 |
| `USB disconnect` 后 `30s`（`12s off + 18s`）才进 `0e8d:2046` / `201c`，隔离后无充电图标 | 物理 5V 已断，冷上电未咬中 Preloader 窗口 | 操作者可以单点重跑 1 次；若仍直出 `2046`，实测 `settle=30s` 或预断电 `2–16m` 均无法稳定恢复（#4063），操作者不得继续盲试，建议人工开机进 ADB 后走 `adb_reboot` |
| `caught_brom=True`，写 `md1img_a` 或 `super` 固定百分比报 `data_mux receive timeout or canceled` | 写大分区阶段 USB 传输中断或线缆/存储异常 | 连续两次同阶段失败时，操作者不得原位继续重跑，建议先换数据线或换空闲槽位 |

## 四、收尾恢复 `0000 off` 端口供电

1. 检查已无待刷设备的每台 Agent host 是否存在残留 `0000 off` 端口：
   `sudo -n uhubctl | awk '/Current status for hub/{h=$5} /0000 off/{print h ":" $2}'`
   预期：列出所有需要恢复供电的端口；无输出则结束本流程。
2. 对每个非待排查 `0000 off` 端口恢复供电并复查：
   `sudo -n uhubctl -l <hub_location> -p <port> -a on`
   预期：端口变为 `0100 power`（或在线设备 `0503 power`），再次执行第 1 步输出为空。
