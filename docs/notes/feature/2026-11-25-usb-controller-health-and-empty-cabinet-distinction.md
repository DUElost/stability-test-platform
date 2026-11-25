# USB 主控/端口硬件体检与空置主机分层区分（2026-11-25）

Status: implemented
Class: feature

## Decision

针对 `/hosts?sync=usb_fault` 中「确认未接手机的空置主机」与「设备真实连接但主控/端口失效导致无 ADB/USB 显示（如 `15.73` xHCI `HC died`）」同形混排、相互掩盖的问题，以及现网只读盘点暴露出的两处 `host_health_probe` 缺陷，实施四项联动改进：

1. **修复控制面 SSH 拓扑对账探针的两处盲区（`backend/services/host_health_probe.py`）**：
   - `reconcile_agent_health`：仅在 `not probe_hc and not probe_link` 时把 `TOPOLOGY_EMPTY_CABINET`（`root_hub_only`）早退判为 `ALIGNED`；当存在 `HC died` 或链路劣化证据时，不再把级联 Hub 全部消失的主控死亡误判成「空柜 / 一致」。
   - `collect_probe_round_via_ssh`：白名单新增只读命令 `journal_kernel_readable`（`journalctl -k --no-pager -o cat --boot --lines=1`，与 Agent 侧 `kernel_log_is_readable` 同构）。当 2h 内核日志返回 `rc=0` 且空 stdout、stderr 无权限盲区提示时，以 `--boot --lines=1` 做正向可读性核验，解决安静主机大面积假报 `probe_error`（现网盘点 14/16 台空树主机命中）。
2. **新增 Agent 零权限 sysfs 主控与端口硬件体检（`backend/agent/device_discovery.py`、`capacity_reporter.py`、`heartbeat_thread.py`）**：
   - 新增纯只读、非递归（避开 `/sys/bus/usb/devices` 下 `peer` 环路）采集函数 `inspect_usb_controller_health()`，通过 `capacity.usb_controller_health` 上报五项硬件硬指纹：
     - `unbound_xhci_controllers`：PCI `0x0c0330` 控制器缺少 `driver` 链接（覆盖 `.88` `probe -110`；不单看 `enable==0`，避免对 `.63` 这类 rebind 恢复后 `enable==0` 但 `driver=xhci_hcd` 在位的正常主机误报）；
     - `disabled_root_ports`：root hub 端口 `disable == "1"`（覆盖 `15.73` `HC died` 后永久禁用态，Agent 重启洗掉 1h journal latch 也不丢失）；
     - `disabled_hub_ports`：外接级联 Hub 端口 `disable == "1"`（覆盖 VBUS `0000 off` 残留 / EMI 禁用）；
     - `over_current_ports`：端口 `over_current_count > 0`；
     - `stuck_default_ports`：级联 Hub 端口停在 `state == "default"` 且无 `device` 节点（覆盖现网 `15.57` `1-5.1-port2` 枚举卡死态）。
   - 收紧 `_usb_tree_empty()` 判据为 `usb_root_hub_count > 0 and usb_device_count == 0 and total_devices == 0`（对齐 `xhci_auto_rebind.is_usb_tree_verifiably_empty`，修正 `usb_device_count <= usb_root_hub_count` 把 1~2 台非 ADB 手机误判为空树的问题）。
3. **前后端统一按「硬件硬证据 > 台账失明 > 零台账纯空柜」分层过滤 `usb_fault`（`backend/api/schemas/host.py`、`backend/api/routes/hosts.py`、`backend/services/host_inventory_query.py`、`frontend/src/utils/api/types.ts`、`HostsPage.tsx`、`ExpandableHostTable.tsx`、`HostDetailDrawer.tsx`）**：
   - `HostOut` 新增只读派生字段 `ledger_device_count`（`device` 表未退役行数，同步 `types.ts`）；
   - `host_usb_fault_priority`（后端与前端同口径）：
     - P3 新增 `unbound_xhci_controllers > 0` / `disabled_root_ports > 0` / `over_current_ports > 0`（硬件证据绝不受 `emptied_at` 或空台账豁免，遵循 ADR-0038 A2）；
     - P2 新增 `disabled_hub_ports > 0`，并要求纯 `usb_tree_empty` 同时满足 `emptied_at is None` 且 `ledger_device_count != 0`（对齐 `StabilityHostUsbBlind`，零台账纯空主机降为 P0、不再刷屏 `?sync=usb_fault`）；
     - P1 新增 `stuck_default_ports > 0`。
   - 前端主行外显新硬件故障芯片（`xHCI 驱动失绑`、`主控端口禁用(N)`、`USB 过流(N)`、`Hub 端口禁用(N)`、`端口卡死(N)`），零台账纯空树显示灰标 `未接设备`，详情抽屉新增「台账在册设备」与「主控与端口体检（sysfs）」。

## Alternatives

- 新增独立 `health.reasons` 枚举项（如 `usb_root_port_disabled`）：暂不引入，避免联动修改 Prometheus 词表/告警规则与四面对拍守卫；复刻 S1 `usb_hub_topology` 的 `capacity` 纯观测字典模式，老 Agent 自动平滑降级且即刻生效于 `/hosts` 工作台与 `facets`。
- 直接在 `stp-agent-priv` 新增 boot 级 `HC died` 摘要子命令：涉及全 fleet `update_agent.yml` 提权 wrapper 下发，属下一步增强；本轮先用零权限 sysfs `disabled_root_ports` + `unbound_xhci_controllers` 达成无需提权即可穿透 Agent 重启的硬指纹识别。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_usb_hub_fine_metrics.py backend/agent/tests/test_capacity_reporter.py backend/agent/tests/test_device_discovery.py tests/test_host_health_reason_surface.py tests/test_frontend_api_types_sync.py -q`（120 passed）
- `TESTING=1 JWT_SECRET_KEY=test-secret ./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_host_health_probe_2983.py backend/tests/api/test_hosts_b2_2_pagination_facets_496.py -q`（隔离 testcontainers PG，24 passed）
- `npm --prefix frontend run type-check && npm --prefix frontend run test -- --run src/components/network/ExpandableHostTable.test.tsx src/pages/hosts/HostsPage.test.tsx`（tsc 0 错误，Vitest 69/69 passed）
- `./scripts/project_python.sh tools/dev/check-internal-ip-leak.py --check -q`（通过，0 泄漏）
- `./scripts/project_python.sh scripts/run_gates.py check:quick`（16 gates 全部通过）
- 全量 `backend/agent/tests/`：3653 passed、2 skipped，仅 1 条与本改动无关的本机宿主环境用例 `test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails` 失败（CI required checks 待 PR 运行）。

## Revisit

- 部署后端/前端并热更新 Agent 后，核验现网 `/hosts?sync=usb_fault`：10 台零台账纯空机自动退出异常视图，`15.57` 浮现 `端口卡死(1)`，`15.12`/`15.67` 保持 `USB 链路劣化`，`15.38`/`15.66`/`15.91`/`15.93`/`15.94` 待现场确认撤机后一键置位 `emptied_at`。
- 如需进一步覆盖「`HC died` 后端口未置 `disable=1` 的极端主板」或「Hub 盒开机前断电无历史报错」，再评估在 `stp-agent-priv` 补 boot 级 `HC died` vs `new USB bus registered` 轻量摘要与 `baseline_cascade_hubs` 漂移检测。
