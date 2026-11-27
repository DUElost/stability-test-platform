# hosts `projection=list` 载荷白名单瘦身与 `1280px` 列表列宽紧凑化

Status: implemented
Class: bug-fix
Related: #496, #4117

## Decision

针对生产实测（`4bbe05dd`）发现的两处体验与载荷瓶颈，在同一次变更中做最小闭环优化：

1. **后端 `GET /api/v1/hosts?projection=list` 白名单瘦身（`backend/services/host_inventory_query.py`）**：
   - 在 `build_host_out_readonly(..., projection="list")` 完成 `status`、挂载摘要标量、`agent_installed*` 与 `build_host_version_view` 派生后，对列表响应执行严格白名单裁剪与 UTF-8 字节有界化：
     - `extra` 仅保留主表资源列与排序所需的数值标量（`cpu_load` / `cpu_cores` / `ram_usage` / `ram_total_gb` / `temperature` / `uptime_seconds` 与 `disk_usage.{usage_percent,total_gb}`），剔除 `heartbeat_timing`、已提升到顶层的重复子树（`capacity`/`self_heal`/`health_probe`/`health`）及 `disk_usage_aee`/`operations`/`archive` 等抽屉与诊断大字段；
     - `capacity` 保留设备/任务计数及 USB 故障优先级/芯片所需的 `usb_hub_topology` / `usb_controller_health`，剔除仅抽屉使用的 `usb_power_slots`、`usb_kernel_errors_1h`、`adb_state_counts`；
     - `health_probe` 保留 `verdict` / `strike_open` / `powered_off_ports`（有界），剔除 `recent_verdicts` / `signals` / `topology`；
     - `self_heal` 保留 `state` / `fuse_open` / `fuse_reason`（有界），剔除 `xhci` / `adb_reconnect` 细目；
     - `health` 保留 `status` / `reasons`（有界）；自由文本字段（`retire_reason` / `emptied_reason` / `host_key_trust`）做 256 B UTF-8 上限保护。
   - `projection="full"`（用于 `GET /api/v1/hosts/{id}` 详情抽屉与默认兼容列表）保持 100% 全量不变。
2. **前端 `1280×800` 展开侧栏时的列表列宽紧凑化**：
   - `ExpandableDeviceTable.tsx`：为此前无宽度上限的 `版本`（`build_display_id`）列增加响应式 `max-w-[136px] xl:max-w-[168px] 2xl:max-w-[260px] truncate` 与完整 `title` 悬停提示，收紧 `< 2xl` 单元格水平内边距与「电量/存储」进度条占位，使 10 列全部落在 `1280px` 展开侧栏后的主表视口内，消除表内横向滚动；
   - `ExpandableHostTable.tsx`：在保留 `host-status-line-*` 单行 `flex-nowrap` 与 `刷机前置` 按钮可见的前提下，收紧 `< 2xl` 单元格 `px-2` 与「主机/设备任务/资源/Agent/操作」列宽，使全部 7 列在 `1280px` 展开侧栏时完整展示、免表内横向滚动。

## Alternatives

- **方案 B：新增独立 `HostListItemOut` Pydantic 模型替换 `HostOut`**：可进一步省去空字段键名，但需新增响应模型、修改路由返回联合类型与前端 `Host` 类型映射；在 `HostOut` 的 `projection="list"` 分支做白名单投影即可将 50 台生产形态主机从 `~337 KiB` 压至 `< 100 KiB` 且零破坏既有契约。
- **方案 C：在 `1280px` 隐藏部分列（如 `刷机前置` 或 `所属主机`）**：会降低运维在常见笔记本分辨率下的一屏可达性；通过限制长 `build_display_id` 宽度并收紧空白留白，无需隐藏任何列即可达成免横滚。

## Verification

- 后端定向回归（隔离测试环境）：`backend/tests/api/test_hosts_b2_2_pagination_facets_496.py` 与 `tests/test_hosts.py`（新增 50 台重主机 `projection=list` `< 100 KiB`、筛选/排序等价性与 `GET /hosts/{id}` 全量保留用例）。
- 前端定向单测：`frontend/src/components/network/ExpandableHostTable.test.tsx`、`frontend/src/components/device/ExpandableDeviceTable.test.tsx`、`frontend/src/pages/hosts/HostsPage.test.tsx`、`frontend/src/pages/devices/DevicesPage.test.tsx`。
- 真机浏览器验证：构建本分支前端并在 `1280×800`（展开侧栏）与 `1440×900` 双视口 × 浅/深双主题下核验 `/hosts` 与 `/devices` 的外层及 `[data-slot="table-scroll-container"]` 均无横向溢出（`scrollWidth <= clientWidth`），并校验真实生产样本构造的 `projection=list` 50 台字节数 `< 100 KiB`。
- 门禁：`scripts/run_gates.py check:quick`。

## Revisit

- 若未来主机列表新增在主行展示的 `extra`/`capacity` 字段，需同步加入 `backend/services/host_inventory_query.py` 的 `_HOST_LIST_*_KEYS` 白名单并验证 50 台载荷仍满足 `< 100 KiB`。
