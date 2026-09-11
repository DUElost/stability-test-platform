# Agent Note: 列表越界/选中集批修复（#822）

Status: implemented
Class: bug-fix
Issue: #822

## Decision

1. **ExpandableDeviceTable**：`filteredDevices` 收缩时渲染期钳回 `safeCurrentPage`，避免越界页空白。
2. **PlanExecutePage / DeviceTablePanel**：`setDeviceTotal` 同步钳回 `devicePage`（外部传 page 的同型父级）。
3. **DeviceMatrix**：shift 连选锚点改存 `device.id`，`applyMatrixSelection` 按 id 再现算 index。
4. **HostsPage**：`visibleSelectedHostIds` 由 `selectedHostIds ∩ liveHostIds` 派生，批量操作与表格展示不携带已删除主机 id。

## Alternatives

- 仅 `usePagination` 内统一钳页：不覆盖 ExpandableDeviceTable 自管 page state。
- PlanRunListPage：已在 #756 单开，本单不重复。

## Verification

- `npm run test -- ExpandableDeviceTable.test.tsx DeviceMatrix.test.tsx HostsPage.test.tsx`
- `python scripts/run_gates.py check:quick`

## Revisit

- `bulkCounts.selected` 仍用 `selectedHostIds.size`；prune effect 后两者一致，若未来 bulk 文案要「仍在线的选中数」可改 `selected.length`。
