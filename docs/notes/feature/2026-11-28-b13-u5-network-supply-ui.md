# B13-U5：前端「网络供给」双 Tab 管理页与 PlanRun 网络供给选择器

Status: implemented
Class: feature

## Decision

按批次 B13（`#4212` v1.3 §3.2 U5）与 ADR-0066 D7 落地前端管理面与 PlanRun 发起网络供给选择器（纯 `frontend/` 改动）：

1. **路由与导航升级（`/network-supply` 主路由 + `/wifi` 兼容重定向）**：
   - `frontend/src/layouts/navItems.ts` 将 `/wifi` 升级为 `{ path: '/network-supply', label: '网络供给', adminOnly: true }`；
   - `frontend/src/router/index.tsx` 在 `AdminRoute` 门控下注册 `path="network-supply"` 与 `path="wifi" -> <Navigate to="/network-supply" replace />`，满足 `#2360` 入口隐藏与路由门控成对约束（`adminRoutes.test.ts` 同步更新）。
2. **「网络供给」双 Tab 管理页（`frontend/src/pages/network-supply/NetworkSupplyPage.tsx`）**：
   - **Tab 1（供给池管理）**：支持按 `resource_type`（`wifi` / `usb_net`）及 `egress`（`direct` 普通直连 / `vpn` VPN 出口，Phase 1 对 `usb_net + vpn` 前端禁用引导）创建、编辑与删除池，统一展示去重并发负载、出口标签、`host_group` 与 `usb_net` 单机软上限；保留 `#2359`（403 权限提示）与 `#2456`（DOM 读值支持密码管理器 autofill）不变量。
   - **Tab 2（已启用供网设备 / 受管会话视图）**：以 `GET /api/v1/network-supply/sessions`（`device_network_session`）为唯一数据源，明示排除非受管硬编码 SSID 与激活前存量 WiFi 任务；展示 `legacy_path=true`（「存量 WiFi 路径」）、`RESERVED`/`PROVISIONING`/`ACTIVE`/`RELEASING`/`RELEASE_FAILED`/`RELEASED`、PlanRun 链接与手动调试信息，并支持**手动开启调试供网**、**续期**、**强制释放**与**专用审计放行**（强制填写 `reason_category` 与 `evidence_ref`）。
3. **PlanRun 发起网络供给选择器（`networkPoolRequirements.ts` / `DispatchCockpit.tsx` / `PlanExecutePage.tsx`）**：
   - 依当前 Plan 全部启用网络步骤（`setup_network`、`connect_wifi`、`monkey_setup`）计算 `required_type` 与 `required_egress` 交集，传入 `GET /api/v1/resource-pools/available` 过滤并展示类型与出口标签；
   - 步骤交集矛盾时在发起区给出明确阻塞提示；发起预览与执行携带 `network_pool_id`（凭据始终留存服务端，不在发起表单采集明文密码）。
4. **类型与客户端同步（`types.ts` / `resourcePools.ts` / `networkSupply.ts` / `index.ts`）**：
   - 新增 `NetworkSession`、`ManualNetworkEnablePayload`、`NetworkSessionAuditOverridePayload` 等类型，并在 `PlanRunCreate` 声明 `network_pool_id?: number | null`。

## Alternatives

- **在原有 `WifiPage.tsx` 内堆叠受管会话逻辑而不迁移路径**：无法响应 ADR-0066 D7.1 将 `/wifi` 正式升级为 `/network-supply` 的路由契约，保留旧目录反而留下双页入口歧义；迁移至 `pages/network-supply/` 并在 `AdminRoute` 下保留 `/wifi` 重定向最清晰。
- **使用原生 `<select>` 做受管会话筛选或自定义手写模态框**：会触犯或偏离全站组件契约（`Dialog` 焦点归位 `#3259`、`Table` 组件与语义色彩令牌）；复用 `@/components/ui/table` 与 `@/components/ui/dialog` 并保持零硬编码色彩调色板。

## Verification

- `./node_modules/.bin/tsc --noEmit`、`./node_modules/.bin/eslint src`、`./node_modules/.bin/vite build`：全部通过。
- 定向 Vitest（`PlanExecutePage.test.tsx` 71、`NetworkSupplyPage.test.tsx` 4、`networkPoolRequirements.test.ts` 5、`Sidebar.test.tsx` 4、`DispatchCockpit.test.tsx` 3、`adminRoutes.test.ts` 3）：6 files / 90 passed。
- 前端与接口契约守卫（`scripts/run_pytest.py tests/test_frontend_filter_workbench_guard.py tests/test_frontend_api_types_sync.py tests/test_api_response_shape_contract.py tests/test_frontend_bulk_selection_guard_2614.py tests/test_frontend_device_table_virtual_guard_83.py tests/test_frontend_sticky_header_opacity.py tests/test_frontend_product_layout.py`）：46 passed（含 0 allowlist 新增）。
- 隔离预览实机验证（`stp-ui-review` @ `ef4f79c9b`）：`/wifi -> /network-supply` 重定向、Tab 1（创建 `wifi` + `usb_net` 供给池、验证 `usb_net + vpn` Phase 1 禁用提示）、Tab 2（受管会话视图与手动开启调试供网弹窗）、`/execution/plan-execute`（含 `monkey_setup` 的 Plan 自动收窄 `required_type=wifi` 并仅召回 WiFi 供给池）在 `1440×900` 与 `1280×800` 下均通过，`docOverflow === 0` 且零 console/HTTP 5xx 报错，验证后已 `ui_review_env.py down` 清理。

## Revisit

- Phase 2 后端解禁 `usb_net + vpn` 后，放开供给池创建表单中的 `usb_net + vpn` 选项及 `analyzePlanNetworkRequirements` 中的对应拦截。
