# Track B P1 收口：设备列表载荷严界（Q2）、主机挂载投影与动态排序快照（Q4）及 #3514 规模验收（Q7）

Status: implemented
Class: bug-fix
Related: #496, #3514, #4117

## Decision

承接 `#4187`（Track B P0 修复），完成独立架构复核剩余三项收口：

1. **Q2 — `DeviceListItemOut`（`projection=list`）真正有界载荷**：
   - 在 `backend/api/schemas/device.py` 为 `DeviceListItemOut` 施加严格的 UTF-8 字节预算：`tags` 最多 16 条、单条 ≤ 64 B、单行标签总预算 ≤ 512 B，长文本字段（`retire_reason`、`build_display_id`、`model`、`product`、`host_name`、`wifi_ssid`、`retired_by`）≤ 256 B，`return_state` 仅保留最多 16 族三键摘要，并新增 `tags_truncated: bool = False` 标记；
   - 保证 50 条合法超长标签记录下原始 UTF-8 JSON 数学上界稳定 `< 100 KiB`；单机详情 `GET /api/v1/devices/{id}` 与旧版 `projection=full` 保持完整原文；
   - 前端 `DevicesPage.tsx` 在执行增量增删标签（`add`/`remove`）遇到 `tags_truncated === true` 时先按需拉取 `api.devices.get(targetId)`，杜绝保存时丢失被列表截断的标签。
2. **Q4 — 主机列表裁剪完整挂载明细与动态排序跨页一致性**：
   - `GET /api/v1/hosts` 新增向后兼容参数 `projection=full|list`（默认 `full` 保护全部旧调用方）：`projection=list` 清空 `mount_status` 明细字典，仅保留 `mount_anomaly`、`mount_count`、`mount_failed_count` 三项轻量标量；`HostsPage` 默认传 `projection=list`，并在打开 `HostDetailDrawer`（无论在页或跨页深链）时始终按需请求 `GET /api/v1/hosts/{id}` 加载完整挂载列表与活跃任务；
   - 在 `backend/services/host_inventory_query.py` 新增有界、线程安全、纯只读（零 DB 写）的动态排序快照表（TTL 120s、上限 256 条），分页响应 `HostPaginatedResponse` 返回 `snapshot_id`；`HostsPage` 翻页时透传同一 `(filter, sort_by, sort_dir)` 代次的 `snapshot_id`，服务端按冻结顺序切片并回填当页最新实时指标，彻底消除 CPU/RAM/Disk/Heartbeat 排名波动导致的跨页重复与漏项。
3. **Q7 — 设备初屏 c10 优化与 `#3514` 规模验收归档**：
   - 在 `backend/services/device_inventory_query.py` 将 `_scope_b_conditions` 中的 `powered_off_slot_keys` 整表 `Host` ORM 读取改为 PostgreSQL 窄列 JSONB 查询（仅查在线且带 `usb_power_slots` 的主机 ID 与槽位子键），并在 `query_filtered_devices_page` 中采用扁平 `COUNT(Device.id)` 与 `defer(Device.extra)` / `defer(Host.mount_status)`；
   - `devices_list_init` c10 p95 从 `226.08 ms` 降至 **`103.81 ms`**，`devices_facets_init` c10 p95 从 `191.42 ms` 降至 **`97.66 ms`**；完整 API / PG `EXPLAIN` / 1,000 格 Minimap 实机 Chromium 证据归档至 `docs/notes/feature/2026-11-26-3514-trackb-scale-acceptance-report.md`。

## Alternatives

- **Q4 使用 keyset cursor 替代快照令牌**：当已展示在第 1 页的高 CPU 主机在翻页间隙骤降至低 CPU 时，keyset cursor (`cpu < last_cpu`) 仍会在第 2 页重复返回该主机；由于 Agent host 总量天然有界（~150 台），冻结 ID 顺序快照是唯一能从数学上保证零重复、零漏项且每页展示实时指标的最小方案。
- **Q2 在数据库写入阶段限制设备标签长度**：会破坏已有长标签数据与历史导入兼容性；在列表投影 DTO 施加读时预算并保留详情接口全文，既满足列表 100 KiB 契约又不影响历史数据完整性。

## Verification

- 后端隔离 PostgreSQL 回归（`scripts/run_pytest.py`）：`test_devices_b2_1_pagination_facets_496.py`、`test_hosts_b2_2_pagination_facets_496.py`、`test_plan_run_b2_3_minimap_pagination_job_83.py`、`test_hosts.py` 共 **82 passed**（含新增 Q2 50 条长标签 `≤ 100 KiB` + 详情原文保留回归、Q4 `projection=list` 裁剪 `mount_status` + 动态 CPU 排序排名颠倒跨页零重复回归）。
- 前端 Vitest：`DevicesPage.test.tsx`、`HostsPage.test.tsx`、`ExpandableHostTable.test.tsx` 共 **3 test files, 109 passed**（含新增 `snapshot_id` 翻页透传与抽屉按需 `api.hosts.get` 回归）。
- 隔离 PostgreSQL 17 规模基准 + 实机 Chromium 验证：见 `docs/notes/feature/2026-11-26-3514-trackb-scale-acceptance-report.md`。
- 仓库门禁：`scripts/run_gates.py check:quick`。

## Revisit

- 若生产控制面未来开启多进程 uvicorn worker（`--workers > 1`），可将 `_HOST_SORT_SNAPSHOTS` 升级为 Redis 短 TTL 瞬时键（遵守 AGENTS.md「Redis 仅承载瞬时跨进程状态」），进程内 LRU 保留为单进程/测试降级后端。
