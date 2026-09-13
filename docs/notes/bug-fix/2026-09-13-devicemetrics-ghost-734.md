# Agent Note: 下线 DeviceMetrics 幽灵功能（#734 Part 1）

Status: implemented
Class: bug-fix
Issue: #734

## Decision

端到端删除 DeviceMetrics 幽灵功能（表 `device_metric_snapshots` 早已被迁移物理删除，后端端点
退化为固定返回 `points: []` 的空桩，前端却仍完整保留消费链路 → 用户点「查看指标」永远看到空白）。

| 层 | 处置 |
|---|---|
| 后端端点 | 删除 `GET /api/v1/stats/device/{device_id}/metrics` 与其 `DeviceMetricsResponse` schema（`backend/api/routes/stats.py`） |
| 后端测试 | 从 `backend/tests/api/test_read_api_auth.py` 的只读端点枚举里移除 `stats_device_metrics` 一例（该文件逐端点断言鉴权，删除端点必须同步） |
| 前端组件 | 删除 `DeviceMetricsModal.tsx`、`DeviceMetricsChart.tsx`（`git rm`） |
| 前端契约 | 删除 `types.ts` 的 `DeviceMetricPoint` / `DeviceMetricsResponse`；`analytics.ts` 的 `stats.deviceMetrics()` 与类型导入；`api/index.ts` 的再导出；`components/charts/index.ts` 的导出 |
| 前端入口 | **两处**触发点都清掉：`DevicesPage`（表格行内入口 + 弹窗状态/渲染）与 `DeviceBulkActionBar`（批量条「查看指标」按钮）；两个组件的 `onViewMetrics` prop 一并移除（否则留下无人消费的死 API） |
| 测试同步 | `DevicesPage.test.tsx` 的 modal mock、`DeviceBulkActionBar.test.tsx` 的「enables metrics…」用例与 handler mock |
| 设计令牌 | `colors.ts` 的 palette 注释：保留第 6 色，但改掉对已删图表的指代（**不回收色位**——回收会让既有图表配色漂移） |

## Alternatives

- **保留空桩端点、只删前端**：不选。issue 的验收项明确包含「移除后端废弃空桩」；且空桩是
  「有端点无数据」的假契约，留着会继续吸引新消费方。
- **回收 palette 第 6 色**：不选。色位是**有序列**，删除中间色会让其后所有图表的取色整体前移，
  属无收益的视觉回归。
- **保留 `onViewMetrics` prop 作为未来扩展点**：不选。幽灵功能的入口已删，留着 prop 就是新的死代码；
  真要重做指标功能时应连数据源一起设计（新端点 + 新契约），而不是复用空桩。

## Verification

- **`check:quick` → 7 gates 全绿**。这里的关键是 **tsc + knip**：删除两个组件后，
  任何遗漏的 import / 导出 / 类型引用都会被 tsc 或 knip 拦下——本改动是「删除型」，
  **引用完整性只能靠静态门禁而不是靠肉眼**。
- `backend/tests/api/test_read_api_auth.py`：该文件按端点列表逐条断言「未认证访问被拒」，
  已同步删除对应用例（详见 PR 的测试段落）。
- 前端受影响用例：`DevicesPage.test.tsx` / `DeviceBulkActionBar.test.tsx` /
  `ExpandableDeviceTable.test.tsx` → 见 PR。
- **Part 2（ActionTemplate 死子系统）已先行完成，本 PR 只是复核取证**：
  - `backend/api/routes/action_templates.py`、`backend/models/action_template.py`、
    `backend/tests/api/test_action_templates.py` **均已不存在**；
  - 迁移链含 `op.drop_table("action_template")`（`f4a5b6c7d8e9_add_action_template_table.py`）；
  - 前端 `utils/api/tools.ts` 已无 `actionTemplates` 导出。
  故本单在 Part 1 落地后**整体可关**。
- **未验证（诚实标注）**：浏览器层观感（不再有「查看指标 / 查看指标历史」入口与空弹窗）。
  本改动为纯删除 + 静态门禁覆盖，未做截图级验证。

## Revisit

- 若产品将来确实要「设备指标历史」：应先恢复数据侧（表 + 采集 + 保留策略），再设计端点与前端契约——
  **不要**复活本 PR 删除的空桩形态（端点存在但恒空）。
- 本单只覆盖 DeviceMetrics 与 ActionTemplate 两处。同类的「表已删、端点留空桩、前端仍消费」形态
  是否还有别处，值得在下次全面评审时用同一判据扫一遍：
  *端点返回恒定空值 + 前端有完整消费链路*。

## 补遗（2026-09-13，生产部署时扫出的一处残留）

Part 1 遗漏了一处**非组件形态**的消费链路：`frontend/src/components/QueryProvider.tsx` 的
`LIVE_QUERY_KEYS` 仍列着 `['device-metrics']`。

- **为何漏**：该处不是「使用查询键」，而是**给查询键设 staleness 默认值**
  （`setQueryDefaults`）。没有组件发 `device-metrics` 查询时它不报错、不产生请求，
  tsc / knip / eslint 也看不见——门禁只覆盖「引用完整性」，覆盖不到「配置指向不存在的东西」。
- **处置**：从 `LIVE_QUERY_KEYS` 移除该键（本 PR）。`design-system/colors.ts` 的 palette 注释
  Part 1 已同步更新，无需再动。
- **Verification**：`tsc --noEmit` / `eslint` / `knip --include files --dependencies` 全 rc=0；
  `QueryProvider.test.ts` 与 `LoginPage.test.tsx` 通过（后者是 `clearAppQueryCache` 的消费方）。
- **Revisit**：判据可扩展为「配置面（staleness / 预取 / 失效列表）指向已删资源」——
  与上面的「空桩端点」判据同源，都是**引用方已消失但被指向方仍留名**。
