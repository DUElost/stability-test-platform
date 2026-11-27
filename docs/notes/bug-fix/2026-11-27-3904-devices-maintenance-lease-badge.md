# /devices 首屏区分测试中、归还欠账与维护占用

Status: implemented
Class: bug-fix
Related: #3904, #4185

## Decision

不改动 DB `Device.status` 枚举与心跳写路径：`BUSY` 仍表示「被任意 ACTIVE 租约占用」。为消解「无运行中任务的 MAINTENANCE 欠账/维护租约被首屏标成测试中」的误导（#3904；并为 #4185 / ADR-0066 的 `manual_network_supply` / `usb_net` 欠账预留），在只读投影层补齐占用原因：

1. **后端**：`DeviceOut` / `DeviceListItemOut` 新增只读 `active_lease_type` / `active_lease_reason`；`device_inventory_query.fetch_active_lease_fields` 对当前页设备 ID 做单次 `IN` 批量查询（零 N+1），由 `GET /devices`（`projection=list|full`）与 `GET /devices/{id}` 同源填充。
2. **前端**：`DevicesPage.toDeviceTableData` 对 `BUSY` 分流——`MAINTENANCE` +（`return_state.*.state === 'debt'` 或 `active_lease_reason === '归还欠账'`）→ `return_debt`（徽标「归还欠账」）；其他 `MAINTENANCE` → `maintenance`（「维护占用」，tooltip 展示 reason）；`JOB`/`SCRIPT` 或旧响应缺字段 → 维持「测试中」。本页选中摘要条与首屏同源；跨页预览仅有 `BUSY` 聚合时改称「占用」，避免误导。
3. **词表**：`status-badge` 的 `device-ui` 增补 `RETURN_DEBT` / `MAINTENANCE` 键，与 `tests/test_status_vocabulary_drift.py` 解析口径兼容。

## Alternatives

- **方案 B：改 `Device.status` 或心跳写路径拆出 DEBT/MAINTENANCE 状态**：会牵动调度、筛选与大量消费者，违反「BUSY=任意租约占用」既有语义，且超出本单清雷范围。
- **方案 C：仅前端根据 `return_state` 推断，不暴露租约字段**：无法覆盖无 `return_state` 的纯维护占用（如未来 `manual_network_supply`），且列表缺 `active_lease_reason` 时 tooltip 无据。

## Verification

- 后端：`scripts/run_pytest.py backend/tests/api/test_devices_maintenance_lease_badge_3904.py`（欠账/JOB 投影 + SQL 常数）；回归 `test_list_devices_sql_count_is_constant_no_host_n_plus_1`。
- 前端：`vitest --run` DevicesPage / ExpandableDeviceTable / status-badge；`npm run type-check`。
- 隔离预览：`tools/dev/ui_review_env.py up --sha <SHA>` + `ui_review_probe.mjs`（1280/1440 无横向溢出）。
- 门禁：`scripts/run_gates.py check:quick`。

## Revisit

- 跨页 `selection-preview` 的 `status_counts` 若需拆分「测试中 / 归还欠账 / 维护占用」，需另开单扩展聚合投影；当前用「占用」避免误导。
- Dashboard「测试中」KPI 与 `/devices` 口径一致性属 issue 验收项，本单未改 Dashboard（不在派单 scope）。
