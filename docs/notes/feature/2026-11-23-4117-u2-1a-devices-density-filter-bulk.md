# U2-1a：DevicesPage 1 号标杆页——首屏密度、单行筛选、批量条收敛与状态/空态缺陷修复

Status: implemented
Class: feature

## Decision

落实 `#4117` Wave 2（1 号标杆页 `DevicesPage`）的前半程 **U2-1a**：

1. **主操作归位与首屏密度重构**：
   - 将「添加设备」主 CTA 从表格筛选条迁回 `PageHeader.action`（保持非 admin 禁用态 + 权限提示）；
   - 将原 92px 五张稀疏数字卡压缩为 38px 紧凑单行状态统计条（`device-status-strip`），保留全部 5 项状态计数、点击反选与 `aria-pressed` 语义；
   - 将 `Product` 与 `系统版本` 下拉收拢至 `MoreFiltersPopover`（`hiddenChips` + `moreActiveCount` 同步计入），主筛选行仅留 5 控件（搜索 + 项目 + 主机 + 型号 + 更多筛选），确保 `1280×800` 与 `1440×900` 均单行不折行；
   - 在 `MoreFiltersPopover` 外部点击与 `Escape` 监听中避让 Portal 挂载的 `[role="listbox"]`，确保嵌套 `SearchableSelect` 可正常选值且首次 `Escape` 仅关下拉。
2. **修复 3 处真实交互缺陷**：
   - **QuickView「需关注(离线/异常)」漏 `error`**：引入 `status=attention`（同步 URL `?status=attention`），同时匹配 `offline` 与 `error` 设备；
   - **渲染期调用 `setSearchParams`**：将失效选项自动复位与越界页码 clamp 从 render 期移至 `useEffect`，渲染期改用纯派生的 `effective*Filter` / `safeCurrentPage`；
   - **0 设备项目空态陷阱**：当存在任意激活筛选条件（含 `?project=` / `stale` / `retired`）时不再卸载 `FilterWorkbench` 进入死胡同空页，改在表格空态行展示并保留完整筛选条与一键重置入口。
3. **行内徽标降噪与批量条收敛**：
   - 移除已映射行上与 `project_key` 重复的常驻「已映射」徽标，仅保留具行动意义的「未映射」中性灰提示；
   - 将 `DeviceBulkActionBar` 的 9 个平铺按钮收敛为 3 个高频主按钮（`批量标签` / `归入项目` / `导出 CSV`）+「更多操作」`DropdownMenu`（`复制序列号` / `滑动留痕` / `退役` / `解除退役`），并移除 `DevicesPage` 上与 `BulkBarSpacer` 重复的 `pb-28`。

## Alternatives

- **将 `DevicesPage` 抽屉迁移（U2-1b）与本 PR 合并提交**：改动面会超过 1200 行，既难复核也不便回归定位；按 Owner 约束拆分为 `U2-1a`（密度/筛选/批量条/缺陷修复）与 `U2-1b`（D2 右侧 `DeviceDetailDrawer` + D5 虚拟滚动 + `#3264` 清理）两步串行交付。
- **完全移除状态统计条仅留 QuickViews**：现有多条单测与高频日常使用依赖 5 态（全部/空闲/测试中/离线/错误）一瞥计数与反选；38px 紧凑单行状态条兼顾完整信息量与 `<= 260px` 首行高度目标。

## Verification

- Vitest 定向回归：`DevicesPage.test.tsx`、`ExpandableDeviceTable.test.tsx`、`DeviceBulkActionBar.test.tsx`（含互斥筛选夹具与「需关注(离线/异常)」往返测试、0 设备项目筛选保留筛选条测试）；
- 后端静态守卫：`.venv/bin/python -m pytest tests/test_frontend_filter_workbench_guard.py`；
- 前端类型检查与 lint：`npx tsc --noEmit`、`npx eslint --max-warnings 0`；
- 全量快速门禁：`.venv/bin/python scripts/run_gates.py check:quick`。

## Revisit

- 紧接 `U2-1b` 完成 D2 右侧标准 `Sheet` 详情抽屉与虚拟滚动改造后，在 `stp-ui-review` 隔离预览做 `1440×900` / `1280×800` × 双主题实机截图对比验收。
