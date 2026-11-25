# Phase 2 Track B 阶段 B3-2：`HostsPage` 切换服务端分页、独立 10 项 Facets 与跨页预览

Status: implemented
Class: feature
Related: #496, #4117, #4151, #4159

## Decision

按已定稿的 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0，§4 / §6.1 / §6.2），将主机集群页（`HostsPage`）从每 10 秒调用 `fetchAllHosts` 全量拉取切换为服务端真分页、独立 10 项 Facets 与显式跨页选择预览，保留共享 `fetchAllHosts` / `fetchHostList` helper 供其余消费方使用：

1. **单页服务端分页与缓存键隔离**：
   - `HostsPage` 停止调用 `fetchAllHosts`，改为按页请求 `api.hosts.listPage`（默认 `limit=50`，页容量选项 `50 / 100 / 200`，默认排序 `sort_by=priority&sort_dir=desc` 对齐后端全局 USB 故障优先级 + `Host.id ASC` 尾键，`keepPreviousData` 避免翻页闪烁）；
   - 使用独立缓存键 `hostKeys.page(params)`，不把 `PaginatedResponse` 写入共享 `hostKeys.list()`（`['hosts']`），同时保留 `hostKeys.list()` 前缀写后失效覆盖。
2. **独立 10 项 Facets**：
   - 顶部状态快捷视图与 Agent 对齐摘要改由 `api.hosts.facets(showRetired)`（`hostKeys.facets(showRetired)`）驱动，仅随 `include_retired` 或写操作失效刷新，翻页、列排序与搜索不再触发 Facets 重拉。
3. **`upgrade_pending` 严格同源谓词（契约 §4 规则 5）**：
   - 「待热更新」快捷视图切换为 `sync_status=upgrade_pending`（`ONLINE && agent_installed && drift`），与 Facets 计数严格同源，不再使用更宽的原始 `sync=drift`。
4. **显式区分当前页勾选与跨页确认（契约 §4 规则 8）**：
   - 表头全选仅勾选当前页并在 `HostBulkActionBar` 标注「仅当前页」；当 `filteredTotal > 当前页已选` 时提供「选中全部筛选结果 (N)」按钮，调用 `POST /api/v1/hosts/selection-preview` 冻结匹配 `host_ids` 与操作计数摘要并标注「跨页已冻结」；
   - 执行批量安装、刷机前置、安全热更新或删除时，仅针对已冻结 ID 集合解析目标并复验实时资格与退役状态，在确认框明确标注跨页范围，不重跑筛选扩大影响面。
5. **跨页 `?drawer=` 深链与筛选空结果保留（契约 §6.1）**：
   - `?drawer=<id>` 指向非当前页主机时经 `api.hosts.get(id)`（`hostKeys.detail(id)`）独立加载并打开 `HostDetailDrawer`；
   - 有活动筛选返回 0 条时保留筛选工作台与表格空行，仅无筛选且在用全集为 0 时展示全页 `EmptyState`（并复用含退役 Facets 探针区分「从未接入」与「全部退役」）。

## Alternatives

- **沿用前端 `fetchAllHosts` 并仅本地分页**：拒绝，无法解除 150 台主机及退役累积场景的全量拉取与客户端重排开销，违反契约 §4。
- **跨页批量动作直接提交筛选条件到后端执行**：拒绝，心跳与状态波动可能静默扩大执行范围；统一走预览冻结 ID → 人工确认 → 显式 ID 执行。

## Verification

- `(cd frontend && npx vitest run src/pages/hosts/HostsPage.test.tsx src/components/network/ExpandableHostTable.test.tsx src/components/host/HostBulkActionBar.test.tsx src/components/ui/bulk-action-bar.test.tsx)` → 4 files, 79/79 passed
- `./scripts/project_python.sh scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py -q` → 8/8 passed
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → 16/16 gates passed
- `stp-ui-review` 隔离栈实机双视口（`1440×900` / `1280×800`）× 双主题 Playwright 探针与截图核验：
  - 全场景 `docOverflow: 0`，3 个主筛选控件高度一致（`36px`，`heightDelta: 0`），分页栏渲染正常；
  - 首屏网络契约通过（单次 `limit=50&sort_by=priority&sort_dir=desc` + 独立 `/api/v1/hosts/facets`，无 `limit=200` 全量拉取，排序不重拉 Facets）；
  - 「待热更新」点击命中 `sync_status=upgrade_pending`（非 `drift`）、0 匹配时保留筛选栏，往返「全部主机」恢复 `aria-pressed="true"`；
  - 详情抽屉打开与 `Esc` 关闭归位通过，核验后已执行 `ui_review_env.py down` 清理预览栈。

## Revisit

- `GET /api/v1/hosts` 旧双形态退出须独立完成调用方盘点与审批；`HostDetailDrawer` 继续保持打开时按需拉取脚本在位矩阵与完整挂载信息，后续如主机元数据继续增长可再评估列表 DTO 裁剪。
