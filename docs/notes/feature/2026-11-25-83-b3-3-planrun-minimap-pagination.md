# Phase 2 Track B 阶段 B3-3：`PlanRunDetailPage` 双视图分离、Minimap 轻量全集保护与单 Job 详情抽屉

Status: implemented
Class: feature
Related: #83, #4117, #4151, #4161

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§5 / §6.1 / §6.2），将 `PlanRunDetailPage` 从单一大载荷 `GET /api/v1/plan-runs/{run_id}/devices` 切换为双视图分离与单 Job 详情抽屉架构，在不截断 Minimap 的前提下解除 1,000 台设备 PlanRun 的载荷与交互瓶颈：

1. **Minimap 独立轻量全集保护（契约 §5.1）**：
   - `usePlanRunDetailData` 常驻消费 `GET /api/v1/plan-runs/{run_id}/devices/minimap`（`planRunKeys.devicesMinimap(id)`），Minimap 永远全量渲染全部设备格子（不做分页或虚拟化），在本地基于 7 字段投影完成即时筛选，清空筛选即恢复全集，保留悬停/无障碍精确异常条数（`log_signal_count`）且不叠加异常红点；
   - 全局心跳超时横幅（`stuck_job_ids`）、Watcher 串号反查、快照抽屉设备选择器（`PlanSnapshotDrawer`）及去重 Host 显示名（含退役主机与无主机桶）均由 Minimap 轻量身份索引驱动，不再由单页表格推导，也不再调用 `fetchAllHosts(true)`。
2. **表格视图按需真分页与聚合 KPI 真实并集筛选（契约 §5.2）**：
   - `GET /api/v1/plan-runs/{run_id}/devices` 仅在切换至 `view=table` 时按需拉取当前页（默认 `limit=50`，页容量选项 `50 / 100 / 200`，`keepPreviousData`），使用 `filtered_total` 驱动分页条而保持 `total` 为 PlanRun 全集口径；
   - `PlanRunKpiGrid` 点击「Job 失联/退避」发送真实并集 `status=lost_or_backoff`（`unknown OR backoff`），点击「设备连接异常」发送真实并集 `link_status=abnormal`（`!= online`），彻底移除旧「仅选首个非零子类」近似逻辑。
3. **单 Job 详情抽屉、延迟子查询与严格隔离（契约 §5.3）**：
   - `DeviceDetailDrawer` 改由 `GET /api/v1/plan-runs/{run_id}/jobs/{job_id}`（`planRunKeys.jobDetail(runId, jobId)`）驱动，保留带 `?planRun=<id>` 归属校验的报告入口与按串号深链日志；
   - 阶段时间线（`/timeline`）与 Watcher 异常明细（`/watcher-signals`）默认折叠、点击展开时按 `(runId, jobId)` 延迟加载有上限子查询（`limit=50`）；
   - 选中单机详情加载成功后仅预取相邻至多 2 条单机详情（绝不预取时间线、异常明细或产物）；表格分页模式切机导航明确标注「本页」边界；通过 `?drawer=<jobId>` 打开不在当前浏览集合中的设备时独立加载单机详情且不猜测邻居；按 `(runId, jobId)` key 隔离抽屉内操作状态与确认弹窗，防止切机残留。

## Alternatives

- **对 Minimap 同样分页**：拒绝，违反 Owner 硬不变量（契约 §5 核心不变量——1,000 台设备态势必须一屏全量可视）。
- **打开抽屉时一并拉取全量 timeline 与 watcher-signals**：拒绝，大长稳任务单机事件可达数千条，必须折叠延迟加载并受分页上限约束。

## Verification

- `(cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx src/hooks/plan-run/usePlanRunDetailData.test.ts src/components/plan-run/DeviceOverview.test.tsx src/components/plan-run/PlanRunKpiGrid.test.tsx src/components/plan-run/DeviceDetailDrawer.test.tsx src/components/plan-run/PlanSnapshotDrawer.test.tsx)` → 6 files, 107/107 passed（含 1,000 格全量 Minimap 渲染与并集筛选恢复、表格 `limit=50` 分页条、`本页` 切机标注、单 Job 详情与相邻 ≤2 预取、延迟加载时间线/异常明细、`?drawer=` 跨页深链独立加载）
- `./scripts/project_python.sh scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py -q` → 8/8 passed
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → 16/16 gates passed
- `stp-ui-review` 隔离栈实机双视口（`1440×900` / `1280×800`）× 双主题 Playwright 探针与截图核验：
  - 真实后端契约核验通过：默认 Minimap 视图单次 `/devices/minimap`、0 次重 `/devices`、0 次 `/hosts`；切换表格视图发送 `/devices?skip=0&limit=50`（`200`）；KPI 并集点击发送 `status=lost_or_backoff` 与 `link_status=abnormal`（均 `200`）；
  - 双视口 × 双主题零横向溢出（`docOverflow: 0`），分页条、Minimap 网格、抽屉「本页」边界与 `Esc` 关闭核验通过，核验后执行 `ui_review_env.py down` 清理预览栈。

## Revisit

- 阶段 B3-1（`#4163` 已合入）、B3-2（`#4164`）、B3-3 全部合入后，同步更新 `#4117` 追踪台账；`PlanExecutePage` 选机池与 `GET /api/v1/plan-runs/{run_id}/devices` 无分页旧形态退出另案独立推进。
