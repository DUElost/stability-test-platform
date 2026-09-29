# Agent 环境前置补 Android USB（adb/fastboot）udev 规则（#3493）

Status: implemented
Class: feature

## Decision

adb 报 `no permissions` 的根因是 usbfs 节点（`/dev/bus/usb/*`）落 Debian 兜底
`root:root 0664`：系统包 `51-android.rules` 只覆盖部分厂商 VID，MLD 等刷机流程
形态（0e8d:2026 等）无人覆盖。fleet 实测 47/48 台裸奔（唯一例外 .87 是 08-24
手工布的 `99-mediatek-adb.rules`，仓库零登记）；#2133 归位 provisioning 链时只
收编了 ttyACM 两条规则，adb usb 面漏收。本 PR 按既有的 #2133/#2284 四面同源
模式收口两个载体面：

| 面 | 改动 |
|---|---|
| `install_agent.sh` §1.3 | `usermod -aG plugdev`（幂等；无 plugdev 组跳过并警告，同 §1.2 dialout 模式） |
| `install_agent.sh` §4d | 写 `/etc/udev/rules.d/90-android.rules`（reason 注释 + 规则行）+ reload + trigger |
| `ensure_flash_prereqs.yml` | plugdev 加组/成员检查 + 两个互补 copy 任务；reload 并集与 restart 门控扩面（`agent_flash_plugdev.changed`）；report 增 plugdev/adb 字段 |
| `update_agent.yml` opt-in 段 | 同源任务（受 `agent_ensure_flash_prereqs` 门控，位于门禁释放之后）；report 扩展 |

**规则取全集而非 VID:PID 白名单（Owner 09-29 裁决）**：

```udev
SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", MODE="0660", GROUP="plugdev"
```

- 本质依据：agent host 的部署约定就是「插上本机的 USB 设备即测试终端」；fleet
  为 MTK 380 + 展锐 188 混编，白名单只覆盖已知 VID:PID，新机型/新模式会原样
  复发 .58 型故障（.87 的窄规则恰是 Honor MTK 专用，不具备 fleet 通用性）。
- 放弃的面：plugdev 对本机所有 usb 节点可写（含 U 盘等非测试设备）。在「内网
  专用机、单一 android 服务账号、机房物理管控」的威胁模型下代价≈0；0660 仍把
  非 plugdev 用户挡在外面。#2284 的最小权限裁决针对 ttyACM 串口的「本地用户干
  扰刷机流」威胁，不适用于 adb usbfs 面。
- `ENV{DEVTYPE}=="usb_device"` 精确化（/dev/bus/usb 节点皆此类型），A1 原文的
  `SYMLINK+="android%n"` 不带（无消费方）。
- 收窄出口写在规则文件注释与测试文件注释里：host 出现不可信本地用户/多租户
  时，按 #2284 同法改回 VID:PID 白名单。

**形态判据沿用 #2353**：「Agent 用户是否已持久属于 plugdev」决定 0660/0666，
不看「组是否存在」；0666 退化时记 warning 并在规则文件注释写明原因。
不扩 wrapper/preflight 面：wrapper `ensure-udev-rule` 只覆盖 ttyACM（D5 固定
内容面不动）；`flash_preflight` 无 adb 判据项，缺失的失败信号由 Plan 运行期的
adb 本身报出（不新增脚本版本）。

## Alternatives

- **VID:PID 白名单（.87 的 99-mediatek-adb.rules 收编）**：否决——见上；白名单
  的安全增益在本威胁模型下≈0，而维护成本是持续性故障复发。
- **照抄 A1 原文（无 DEVTYPE、带 SYMLINK）**：部分采纳——规则行语义同 A1，补
  DEVTYPE 精确化；SYMLINK 无消费方不带走；文件名沿用 A1 的 `90-android.rules`
  （fleet 内一个名字，.87 收口时替换其手工文件并清理悬空的同名文件）。
- **热更新通道承载**：否决——#2133 已裁定常规热更新不碰系统面；本改动走
  install 链 + 刷机前置通道（`POST /hosts/{id}/flash-prereqs/ensure`）。
- **preflight 增加 adb 判据项**：本切片不做——需新脚本版本 + DB 注册 + pin 同
  步（script-versioning SOP 全链），且失败信号已由 adb 本身报出；如需可在后续
  单独立单。
- **playbook 里加 `adb kill-server`**：否决——fleet 基础镜像的 android 用户本
  就属 plugdev，运行中的 adb server 组集合已含 plugdev，udev trigger 后重扫即
  接管设备；仅「加组实际变更且 server 在跑」的罕见场景需 kill-server（由重启
  agent 的既有任务间接覆盖新起的 server；存量 server 由运维一次性 kill）。

## Verification

- `pytest tests/test_flash_provisioning_prereqs_2133.py tests/test_install_agent_artifacts.py
  tests/test_install_agent_noninteractive.py tests/test_agent_priv_boundary.py
  tests/test_agent_priv_flash_primitives.py tests/test_ansible_digest_contract.py
  tests/test_ansible_digest_bookkeeping.py tests/test_shell_line_endings.py -q`
  → **139 passed**（含新增 5 条：§1.3 加组、§4d 双形态+成员判据、update/ensure
  playbook 的内容同源+互补门控+reload/restart 并集）；
- §4d 逻辑沙箱实测（块路径重写到临时目录，未触碰 `/etc`；stub `id` 变成员状态）：
  - plugdev 成员 → 写出 `GROUP="plugdev", MODE="0660"` + 收窄出口注释；
  - 非成员 → 写出 `MODE="0666"`、打 warning、注释写明退化原因；
- `bash -n backend/agent/install_agent.sh` 通过；三个 playbook `yaml.safe_load`
  通过（copy content 解析后与测试常量逐字一致——初版单引号 YAML 不转义 `\n`
  被测试当场拦下，改双引号转义后绿）；
- 现场只读基线（09-28，#3493 issue 内）：.58 节点 `root:root 0664`、设备
  0e8d:2026 全 no permissions；.87 节点 `root:plugdev 0660`、adb `device` 态正常；
  47/48 台无 adb usb 规则文件。

## Revisit

- **勘误（2026-09-29 当日两轮实测，.58 首台走正规通道发现）**：① playbook 的
  `udevadm control --reload` 只重载规则库，**已插设备的节点不会重算**——.58 上
  规则落盘后 `/dev/bus/usb/*` 仍 `root:root 0664`、adb 仍 no permissions。ttyACM
  时代靠设备重枚举/装机时序掩盖了这一点；本规则的目的恰是修「已插着」的设备。
  ② trigger 若按「本轮规则 changed」门控，会在「规则文件已在（首轮落盘、copy
  全 no-op）而设备仍是旧节点」的复查场景被 skip——那恰是本通道最要修的状态。
  故 trigger **不按 changed 门控**（ensure 链无条件跑；update 链只受 opt-in
  开关门控），uevent 重放幂等、不复位设备、不断开连接。android 用户的免密
  sudo 只有 wrapper + 固定 systemctl（ADR-0037），该步骤必须由 playbook
  （become）承担，无法在主机上手工补。parity 测试钉「trigger 紧跟 reload、
  不含 changed 条件」。
- **存量机收敛是部署侧动作（pending）**：PR 合入后对 47 台走刷机前置通道
  （`POST /hosts/{id}/flash-prereqs/ensure` 或 `ensure_flash_prereqs.yml`），
  验收 = 节点 `root:plugdev 0660` + `adb devices` 出 `device`；.87 收口时用
  标准规则替换手工的 `99-mediatek-adb.rules`/`99-usb-autosuspend.rules` 并清理
  其悬空的 `90-android.rules`；
- **playbook 收敛后**：运行的 adb server 若组集合陈旧（仅加组实际变更的机器）
  需 `adb kill-server` 一次；udev trigger 后已具资格的 server 自动重扫接管；
- **preflight adb 判据面**：如需「刷机前置检查项」显式化（而非靠 Plan 运行期
  adb 报错），另立单走脚本版本链；
- **6d 文档漂移**：`docs/linux-agent-ansible-runbook.md` 若枚举 provisioning 步
  骤需补 §1.3/§4d 一行（本次未动 runbook，仅 onboard skill 坑表触点）。
