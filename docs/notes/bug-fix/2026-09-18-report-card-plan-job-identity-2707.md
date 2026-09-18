# 报告页「计划信息」块：去掉 Plan 与 Job 的实体混用（#2707）

Status: implemented
Class: bug-fix

## Decision

`/jobs/:jobId/report`（#2420 确立的权威形状）的「任务信息」卡里，**「任务ID / 类型」
绑的是 Plan（`report.task`，`type` 字面量至今为 `"PLAN"`），而「状态」绑的是 Job
（`report.run.status`）**；页标题与面包屑又是 `Job #…`（`report.run.id`）。于是面包屑
写着 `Job #559`、正下方第一行写着「任务ID 5」——同一个数字位上是两个实体。

按 issue 的**最小方案**只做命名澄清（不动 `report.task` 载荷，避免波及缓存键与
Jira 面板取数）：

- 卡片标题「任务信息」→ **「计划信息」**，「任务ID」→ **「Plan ID」**（类型保留，
  值本就是 `PLAN`）；
- 新增一行 **「Job ID」**（同源 `report.run.id`，不新增请求）——让这张卡自身不再
  需要读者去猜「哪个 id 指谁」；
- 页头副标题「任务: …」→ **「计划: …」**（同一页不再出现两种含义的「任务」）。

**不做**：「任务」一词的全局口径统一属 #2546 的语义所有权裁决范围，本单只保证
**这一页**不再同时使用两个含义。

## Alternatives

- **改 `report.task` 载荷（重命名/换绑成 Job）**：否决。那是后端契约变更，会波及
  缓存键、Jira 面板与导出；本单的收益（命名不再误导）用界面文案即可拿到。
- **只加 Job ID、不改卡片标题与行名**：否决。那只增加一行，读者仍要问「任务ID 是
  谁」；混用形态本身才是缺陷。
- **顺手把全站「任务」都改成「计划」**：否决（越界）。#2546 正在做语义所有权评审，
  本页的澄清不预设那里的结论。

## Verification

- 新增用例 `RunReportPage 实体归属（#2707）`：断言「计划信息」块里 `Plan ID` 行
  取 `task.id`（fixture=1）、`Job ID` 行取 `run.id`（fixture=3），且旧文案
  `任务信息` / `任务ID` 不再出现。
- **反例构造（先证伪再采信）**：① 文案回退为「任务信息 / 任务ID」→ 用例 **FAILED**；
  ② 把 Job ID 行绑成 `report.task.id`（两个实体绑反）→ 用例 **FAILED**。恢复后通过。
- 实测：`npx vitest run src/pages/runs/RunReportPage.test.tsx` → **12 passed**；
  前端全量 `npx vitest run` → **128 files / 1035 tests passed**；
  `python scripts/run_gates.py check:quick` → **[OK] (12 gates)**。

## Revisit

- **全局「任务」口径**：本页已澄清，其余页面（含 Jira 面板、导出文案）是否也混用，
  随 #2546 的语义所有权评审一起裁决。
- **导出/API 面**：`/runs/{id}/report/export` 的 markdown 里同样有 `report.task`
  的字样；若用户在导出件里也遇到同一混淆，按同一「只改文案」的口径处理即可。
