# S4：xHCI 主控与 L3 批量 ADB Offline 门控自愈与持久化熔断闭环（#2972 / ADR-0037 D9）

Status: implemented
Class: feature

## Decision

依据 Owner 2026-11-23 对 ADR-0037 v0.8 D9（PR #4114，经第 2 轮独立安全复核通过）的正式裁定，落地主机 USB/Hub/ADB 门控自愈与熔断升级全链路（切片 S4）：

1. **提权窄子命令（`backend/agent/stp_agent_priv.py`）**：
   - 新增无目标参数子命令 `xhci-rebind`（`allow_abbrev=False`，不经 shell，非 root 退出码 2，不新增/修改 sudoers）。
   - 逐级以 `O_NOFOLLOW | O_DIRECTORY` 打开 `/sys/bus/pci/drivers/xhci_hcd`，仅枚举符合 PCI BDF 正则的符号链接并校验 `realpath` 落于 `/sys/devices/` 下且末段等于该 BDF；仅以 `O_WRONLY | O_NOFOLLOW` 向驱动目录下普通属性文件 `unbind` / `bind` 写入 BDF 名（等待间隔 `2.0s`、总超时 `30.0s` 含 `SIGALRM` 兜住内核同步 probe 阻塞）。
   - 重绑后核验驱动目录重新出现对应 BDF 链接且以 `O_RDONLY | O_NOFOLLOW` 读已校验真实 PCI 目录下 `enable != 0`；失败 / 部分成功 / 超时 / `incident §6.1 probe -110` 一律 exit 3 并向 stderr 输出 `STP_XHCI_REBIND_*` 词表标记与逐 BDF 结构化摘要。
2. **Agent 自愈协调与防洗白熔断（`backend/agent/xhci_auto_rebind.py`、`backend/agent/self_heal_coordinator.py`）**：
   - 新增 `PersistentFuseStore`：按 `startup_identity.read_boot_id()` 原子持久化（`0600` + `fsync` + `os.replace`）到 `BASE_DIR`（热更新同步目录 `agent/` 之外），同 `boot_id` 完整恢复、跨 `boot_id` 清 boot 计数但保留 48h 窗口、文件损坏/非普通文件/权限异常时 fail-closed 置 `fuse_state_corrupt`（`need_human`）。
   - 新增 `ClaimPauseGate` 接入 `claim_loop.process_claim_tick`：触发自愈前暂停 `effective_slots` 与 `vbus_slots` 认领并等待在途 claim tick 退出，持闸复核 `occupancy`、`local_db` 活跃作业、快照鲜度（≤ 1 心跳周期）与维护窗，并保持暂停直至 `15s` 重枚举冷却结束。
   - 调用 `xhci-rebind` 前先 `record_attempt` 落盘；收到 `STP_XHCI_REBIND_PROBE_FAILED` 立即转 `hardware_suspect` 不空转重试；L3 批量 `adb offline` 非提权自愈同构复用 `ClaimPauseGate` 与独立持久化 `adb_reconnect_fuse.json`。
3. **控制面与 `/hosts` 升级闭环**：
   - `/api/v1/heartbeat` 响应向 Agent 返回只读门控线索 `host_ledger_device_rows` 与 `in_maintenance`；`HostOut.self_heal` 与前端 `types.ts` 同步暴露 `host.extra["self_heal"]`。
   - Prometheus 新增 `stability_host_self_heal_fuse_open{host_id,state}` 与告警 `StabilityHostSelfHealFuseTripped`（附 `alerts-stability-platform.test.yml` 场景）。
   - `/hosts` 主行置顶与内联芯片外显 `自愈熔断(疑似硬件)` / `自愈熔断(需人工)`，展开诊断卡展示熔断态、计数与最近标记。

## Alternatives

- **仅内存 `RebindFuse`**：否决，常态 Agent 热更新/重启会洗白同 boot / 48h 计数（复核第 1 轮 C2）。
- **让 `xhci-rebind` 接受调用方传入目标 BDF**：否决，违反 ADR-0037 D6/D9 最小权限面。
- **在 S4 PR 中直接改 `ADR-0037` §4**：延后至 PR #4114 合入 `main` 后单独同步，避免与在途文档 PR 产生合并冲突。

## Verification

- `pytest tests/test_agent_priv_xhci_rebind.py tests/test_agent_priv_parser_contract.py backend/agent/tests/test_self_heal_coordinator_s4.py backend/agent/tests/test_xhci_auto_rebind_2972.py backend/agent/tests/test_adb_reconnect_selfheal_2754.py tests/test_prometheus_alerts_contract.py`
- `vitest run src/components/network/ExpandableHostTable.test.tsx`
- `scripts/run_gates.py check:quick`

## Revisit

- PR #4114 合入 `main` 后，同步将 `ADR-0037` §4 D9 占位转正并登记本 PR 实施验收记录。
- 生产灰度必须严格按 ADR-0037 §5 Revisit #6 执行：先经 `update_agent.yml` 逐台下发新版 `stp-agent-priv`，再以 `STP_XHCI_AUTO_REBIND=1` + `STP_XHCI_AUTO_REBIND_HOSTS` 做 1 → 5 → 全量灰度（默认关闭，合入不改变现有生产行为）。
