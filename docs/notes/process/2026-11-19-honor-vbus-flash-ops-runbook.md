# Honor P535AA VBUS 刷机运维规程与排障沉淀

Status: implemented
Class: process

## Decision

在 `docs/operations/honor-flash-runbook.md` 新增 §10「VBUS 切电触发模式（`trigger_mode="vbus_cycle"`）运维规程与排障」，将 2026-11-18 至 2026-11-19 两批次 9 台 Agent host 共 141 台 `MLD_NX2` / `MLD_NX3` 设备实操中验证稳定的做法固化为操作规程：

1. 前置检查与槽位重刷：明确通过 `lsusb -t` 检查 `Bus 002` 无 `0bda:0411` USB 3.0 伴生 Hub、确认 `stp-agent-priv` 具备 `usb-power/expect-slot` 能力、换插物理 USB 口后必须重采 sysfs 串号并经 `ensure_usb_power_slots.yml` 同步 `/etc/stp-agent/usb-power-slots.json`，并用 12 秒单口切电配合 `journalctl -k` 核验实际产生 `0e8d:2000`。
2. 调度与控制面护栏：按每批不超过 2 台、前批终态后再推下一批的串行节奏避免触发 15 分钟 `pending_timeout`，并在放量前检查控制面负载/磁盘 I/O（避开 #4054 重 I/O 争抢）。
3. 真假断电时间指纹与止损规则：沉淀 `17–18s` 假断电（含同为黑色 USB 口仅断 D+/D-、不断 5V 的 `.69` 实证与设备自身状态异常的 `.75` 实证）、`30s` 真断电错过 Preloader 窗口（含 `vbus_off_settle_seconds=30` 及 2–16 分钟预断电均未能稳定拉出 `0e8d:2000` 的止损边界，见 #4063）以及大分区 `data_mux receive timeout` 的处置规则。
4. 收尾供电恢复：明确批次结束后必须排查并恢复各 Agent host 残留的 `0000 off` 端口（见 #4063）。

本次只改动 `docs/operations/honor-flash-runbook.md` 新增章节（未改动代码与已有 §0–§9 句子），按 `docs/development/writing-conventions.md` §2–§5 检查新写句子。

## Alternatives

- 仅另写一篇 `docs/operations/incident-2026-11-19-*.md` 事故复盘：复盘类文档不便于一线按操作顺序执行，且运维日常直接查阅 `honor-flash-runbook.md`，因此将可执行 SOP 与实测分型直接落入 `honor-flash-runbook.md` §10，并在本 note 记录变更依据。
- 同时修改 `flash_firmware.py` 放开 `vbus_off_settle_seconds` 上限或自动恢复 `off` 端口：前者已实测 2–16 分钟预断电仍未稳定改善，后者属于 #4063 待讨论设计决策，故本次先沉淀文档，不提前改动运行代码。

## Verification

- 按 `docs/development/writing-conventions.md` §5 逐项核对新增 §10 的术语、规则句强度词（仅用 `必须 / 不得 / 建议 / 可以`）、SOP 一步一动作与警告前置；
- 确保新增内容不含真实内网完整 IPv4、凭据或主机清单细节；
- 在 worktree `.wt/docs-honor-vbus-runbook` 内运行 `./venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- 当 #4063 关于「VBUS 失败 `0000 off` 隔离端口的平台可见性与自动恢复」或「关机态 VBUS 上电无法停入 `0e8d:2000`」有明确工程结论并落地新版本脚本时，同步修订 `docs/operations/honor-flash-runbook.md` §10.3 与 §10.4。
