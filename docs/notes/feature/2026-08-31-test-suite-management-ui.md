# ADR-0030 P2 用例管理前端（#429 块 1）

Status: implemented
Class: feature

Issue #429 拆两块：本 Note 只记**块 1**（纯前端，零后端改动）——套件列表 /
详情 / 用例 CRUD / import·export·validate·export-to-tool-dir。块 2
（`test_case_result` 表 + NFS 摄入）未做。

## Decision

- **路由**：`/test-suites`、`/test-suites/:suiteId`；侧栏「资源 → 用例套件」
- **API 客户端**：`frontend/src/utils/api/suites.ts` 补齐 14 端点镜像；`suiteKeys`
  工厂 + 变更时 invalidate `suites-for-plan-editor`
- **漂移展示**：列表 `export_stale` badge；详情对比 `content_sha256` vs
  `exported_content_sha256`（库漂移 vs 磁盘导出物漂移）
- **用例编辑**：`exec_descs` 以 JSON 数组 textarea 整覆盖（对齐 P1 §7 #4）

## Alternatives

- **块 1+2 同 PR**：拒绝——块 2 需迁移与摄入链路，与前端页正交
- **Radix 表单组件**：沿用项目登记簿 / 脚本库的原生 select + Dialog 模式

## Verification

- `npm run type-check`
- `vitest --run src/pages/suites/TestSuitesPage.test.tsx`（列表 / 导航 / 创建）

## Revisit

块 2：`test_case_result` + PlanRun 逐条结果卡；块 1 可补 EditSuite 元数据对话框、
global.xml 与 runtask 成对导入 UI。
