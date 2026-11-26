# uhubctl 刷机前置并轨、VBUS 断电态可视化与纯空树 72 小时自动消噪

Status: implemented
Class: feature

## Decision

1. **刷机前置并轨安装 `uhubctl`**：在 `tools/ansible/group_vars/linux_hosts.yml` 新增独立包清单 `agent_flash_prereq_packages_vbus: [uhubctl]`，在 `ensure_flash_prereqs.yml`（主机页「刷机前置」入口）与 `update_agent.yml` 的 `agent_ensure_flash_prereqs` opt-in 段一并安装，同时保持原有 V5/V6/Unisoc 包集与已发布 `flash_preflight.py` 脚本的契约一致（不触发 ADR-0051 不可变脚本修改）。
2. **控制面探针采集 `uhubctl` 断电端口（`0000 off`）并在 `/hosts` 独立展示**：
   - 在 `backend/services/host_health_probe.py` 白名单加入 `("timeout", "8", "uhubctl")`，对存在 `unbound_hubs > 0` 的主机显式跳过（规避半枚举 SS Hub 下 `uhubctl` 阻塞 20–30s 的已知现象）；未安装（rc=127）或超时（rc=124）仅记录 `uhubctl_status`，绝不污染 `lsusb/journal` 对账 `collect_failed`。
   - 对 `0000 off` 端口按主机任务态防抖：空闲主机（`active_jobs <= 0`）单轮确认；有在跑任务主机（`active_jobs > 0`）需连续两轮命中才确认，避免把 12 秒 `vbus_cycle` 刷机切电误报为持久断电。
   - 在 `/hosts` 列表以独立 info 色芯片 `VBUS 断电(N)`（P1 优先级，与 OS 层 sysfs `Hub 端口禁用(N)` 明确区分）展示，并在详情抽屉列出具体 `hub:port` 及 `capacity.usb_power_slots` 关联串号。
3. **纯 `usb_tree_empty` 按 72 小时台账活跃窗口自动消噪**：
   - 在 `host_inventory_query.py` 与 `HostOut` 新增只读字段 `ledger_latest_seen_at`（单次 `GROUP BY` 统计 `MAX(COALESCE(Device.last_seen, Device.created_at))`，零额外 DB 查询）。
   - 硬件体检全绿的纯 `usb_tree_empty` 仅在 `emptied_at IS NULL` 且台账设备最近活跃时间 ≤ 72 小时内时计入 `usb_fault`（P2）；超过 72 小时自动归为「长期空置」（P0），大幅减少人工逐台置位 `emptied_at` 的运维负担。任何硬件硬证据（`usb_controller_health`、`usb_link_degraded`、`unbound_hubs`、`HC died`）与 `uhubctl` VBUS 断电均不受 72 小时窗口豁免。

## Alternatives

- **在 Agent 5 秒心跳线程内直接执行 `uhubctl`**：放弃。`uhubctl` 需要 libusb 特权访问，且在存在半枚举 SuperSpeed Hub 的主机上会阻塞 20–30 秒，拖慢心跳与设备发现；复用控制面 10 分钟 SSH `host_health_probe` 无需扩展 Agent 特权面且不碰心跳链路。
- **修改 `flash_preflight.py` 把 `uhubctl` 塞进 `_DEFAULT_PACKAGES`**：放弃。依据 ADR-0051 已发布脚本版本不可原地修改，改为新增独立 `agent_flash_prereq_packages_vbus` 变量即可兼顾 UI 一键补装与旧脚本版本稳定。
- **靠人工给每台历史空柜逐一点击「标记设备面已处置」**：放弃。台账中历史离线设备若未重新归属或退役会永久挂在原主机，长期增加管理负荷；按 72 小时活跃窗口对硬件全绿空树自动降级既不漏报突发掉光，又免去人工清理历史空柜。

## Verification

- `unset TEST_DATABASE_URL && bash scripts/project_python.sh scripts/run_pytest.py tests/test_flash_provisioning_prereqs_2133.py backend/tests/services/test_host_health_probe_2983.py backend/tests/api/test_hosts_b2_2_pagination_facets_496.py`
- `cd frontend && npx tsc --noEmit && npx vitest run src/components/network/ExpandableHostTable.test.tsx src/pages/hosts/HostsPage.test.tsx`
- `bash scripts/project_python.sh scripts/run_gates.py check:quick`
- `bash scripts/project_python.sh tools/dev/check-internal-ip-leak.py`

## Revisit

- 若后续引入级联 Hub 基线（`cascade_hubs` 从 >0 跌落为 0 的整盒断电检测）或接入 `uhubctl -a on` 一键恢复供电操作，可在 `host_health_probe` 与 `HostDetailDrawer` 的 `VBUS 供电（uhubctl）` 区块直接演进。
- 72 小时纯空树活跃窗口阈值当前固化为 `EMPTY_TREE_RECENT_LEDGER_HOURS = 72`，若未来长稳专项出现 >72 小时无人值守且希望纯空树继续挂红的需求，可评估配置化。
