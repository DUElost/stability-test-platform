# 沉淀刷机与 USB 排障 SOP 实操边界（2026-11-23）

Status: implemented
Class: process

## Decision

将 2026-11-19 至 2026-11-23 批量 P535AA 刷机与 `.73`/全机队排障期间实证的 6 项运维边界直接沉淀到对应按需 Skill 与 Runbook 中（承接并补充 [`2026-11-19-vbus-flash-ops-skill.md`](./2026-11-19-vbus-flash-ops-skill.md) 与 [`2026-11-19-honor-vbus-flash-ops-runbook.md`](./2026-11-19-honor-vbus-flash-ops-runbook.md)），并按 `docs/development/writing-conventions.md`（ADR-0059）统一补齐显式行为主体与规则动词：

1. **V6 大包 NFS 并发与 `abort` BROM 滞留边界**（`docs/operations/honor-flash-runbook.md` §5）：无本地 SSD 缓存拉取 V6 等 6–9 GB 固件包时，建议分批控制同时拉包的 Agent host 并发并把单轮 `timeout_seconds` 从 `1200` 调高至 `2400`；非 `vbus_cycle` 模式下若设备已进 `0e8d:2000`，`abort` 的 `SIGTERM` 清理无法经 ADB 重启，操作者必须通过现场手动开机或硬件断电恢复。
2. **`0000 off` 端口恢复与免密窄面口径**（`.claude/skills/vbus-flash-ops/SKILL.md` §四、`docs/operations/honor-flash-runbook.md` §10.4）：检查改为直接执行 `/usr/sbin/uhubctl`（缺包时改用非递归 sysfs `*-port*/disable` 通配），将恢复供电与复查拆为独立步骤；恢复优先走 `sudo -n /usr/local/sbin/stp-agent-priv usb-power --serial <serial> --expect-slot <hub_location>:<port> --action on`，仅未映射端口使用带密码的 `sudo /usr/sbin/uhubctl`。
3. **`diagnose-device-stall` 实操守卫扩展**（`.claude/skills/diagnose-device-stall/SKILL.md` 第 10 步与「踩坑守卫」）：
   - L1 `HC died` 核验补充 `usb1-port*`/`usb2-port*` 的 `disable=1`、`stp-agent-priv read-kernel-log --boot --lines 5000` 取证，以及「Agent 重启后 `usb_kernel_log` 回 `"ok"`」和「PCI `unbind`/`bind` 不在 `NOPASSWD` 内」两条警示；
   - 禁止对 `/sys/bus/usb/devices` 执行 `find -L`（环形 `peer` 符号链接导致超时），且在 `usb_link_degraded` 主机建议对 `lsusb`/`uhubctl`/`adb` 加 `timeout 5`；
   - 明确控制面 `/hosts` 的 `ONLINE` 心跳与入向 SSH 无关、`hosts.ini` 凭据须按 `[android:vars]` 解析、`android` 用户的 `adb devices` 只查自身 5037 端口而平台识别数须以控制面 `GET /api/v1/hosts` 的 `capacity.adb_state_counts` 为准。

## Alternatives

- 仅留存会话记录或单独写一篇事故复盘不改既有 Skill / Runbook：一线排查与刷机时直接读取 `diagnose-device-stall`、`vbus-flash-ops` 与 `honor-flash-runbook.md`，分散文档无法在操作路径上拦截 `find -L` 卡死、`sudo -n uhubctl` 失败与重启掩盖 `usb_kernel_log` 等重复踩坑。
- 直接覆写 `2026-11-19-vbus-flash-ops-skill.md`：本次新增内容横跨 `diagnose-device-stall` 与非 `vbus_cycle` `honor-flash-runbook.md` §5，独立新增本 note 并交叉链接 2026-11-19 两篇 note 更符合主题边界。

## Verification

- `./scripts/project_python.sh tools/dev/check-internal-ip-leak.py --check -q`
- `./scripts/project_python.sh scripts/run_pytest.py tests/test_skill_type_registry.py -q`
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
- `./scripts/project_python.sh scripts/run_gates.py check:pr`（在 worktree `.wt/docs-flash-stall-sop-20261123` 内于提交前运行）

## Revisit

- 当本地 SSD 固件缓存（Host Firmware Cache）全量上线后，重议 `honor-flash-runbook.md` §5 关于无本地缓存拉取 V6 大包的并发与 `timeout_seconds` 建议。
- 当 `stp-agent-priv` 授权面或 `/hosts` 展示字段变更时，同步核对 `.claude/skills/diagnose-device-stall/SKILL.md` 与 `.claude/skills/vbus-flash-ops/SKILL.md`。
