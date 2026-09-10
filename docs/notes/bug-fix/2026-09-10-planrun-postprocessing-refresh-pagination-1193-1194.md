# PlanRun 终态刷新与结果分页（#1193/#1194）

Status: implemented
Class: bug-fix

## Decision

**#1193（R12-F06）**：终态后 scan/upload/merge/extract 与逐条用例入库仍可能继续，
但页面此前在终态全面停轮询，且「刷新」不覆盖后处理产物查询。修正：

- 三个后处理产物查询（DLE 事件、去重状态、逐条用例结果）挂 `SLOW_REFETCH_MS`
  （30s）慢轮询：终态后产物到达仍可持续展示；标签页失焦时 React Query 默认暂停
  轮询，页面关闭即停止——以「页面在场」为轮询上界（前端无可用的 run 级后处理
  完成信号，不发明停更条件；出口见 Revisit）。
- `refreshAll` 键表抽成纯函数 `planRunRefreshKeys`，补入 `dedupKeys.status` 与
  `planRunKeys.testCaseResults`；回归断言键表不缩水。
- 删除 `usePlanRunDetailData` 中与 LogEventsCard 同 key 的重复 `logEventsQ`
  （死代码，且其 `refetchInterval:false` 会掩盖卡片轮询语义）。

**#1194（R12-F07）**：两卡固定 limit（200/500）且无分页/截断提示，第 201/501 条
不可达。修正：

- 两卡实现「加载更多」：按页放大 limit（200/500）；查询键纳入 `{limit}`
  （`planRunKeys.logEvents/testCaseResults` 扩展 opts，前缀不变，refreshAll 按
  前缀失效仍兼容）；头部显示「已显示 X / total」，超出时给加载更多按钮，不再
  静默截断。

涉及：`hooks/plan-run/{planRunDetailUtils,usePlanRunDetailData}.ts`、
`components/plan-run/{LogEventsCard,TestCaseResultsCard,DedupReportCard}.tsx`、
`utils/api/queryKeys.ts` + 对应测试。

## Alternatives

- #1193 按「后处理完成条件」停更：前端拿不到 run 级完成信号（`job.post_processed_at`
  未暴露），精确停更需后端新增字段——本次不扩后端，用页面在场慢轮询 + 手动刷新，
  在 Revisit 留出口。
- 追加式分页（offset append 合并多页）：需自管数据合并与失效；放大 limit 的窗口读
  更简单且贴合后端 skip/limit 契约（单 Run 百级～千级记录）。
- 用 fake timers 写轮询行为测试：React Query 的 timeout 管理器与
  `vi.useFakeTimers` 不兼容（实测 5s 超时），改为断言查询实际配置的
  `refetchInterval` + 键表纯函数测试。

## Verification

- `npx vitest run src/components/plan-run/ src/hooks/plan-run/ src/pages/execution/`
  → 17 文件 / 179 用例通过
- 红绿：未修复卡片上 4 个新用例失败（两卡 load-more、空态、慢轮询断言），
  修复后通过
- `npm run type-check`、`eslint src --max-warnings 0`、`npm run build` 通过

## Revisit

- 若后端暴露 run 级「后处理完成」信号（全部 job `post_processed_at` + archive
  完成），可把慢轮询改为按完成条件停更，去掉「页面在场」上界；
- 若单 Run 明细增长到万级，limit 放大式窗口读需改为 offset 追加/虚拟列表。
