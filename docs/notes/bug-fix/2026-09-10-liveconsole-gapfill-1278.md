# LiveConsole 重连回填：补齐 in-flight 缺口 + 切 run 丢弃迟到结果（#1278）

Status: implemented
Class: bug-fix

## Decision

`frontend/src/components/console/LiveConsole.tsx`（#1116 引入的间隙回填）两处缺口：

1. **in-flight 期间丢批次**：`onMessage` 收到 `from > expected` 时调用 `fillGap()`
   并丢弃该批；若此时回填请求在途，`fillGap()` 直接 `return`。后端先落盘再 emit，
   所以被丢的批次可从服务端日志补回——但只有在**再次触发回填**时才会发生；run
   随即转终态/静默时该行段永久丢失。
2. **无 run 切换守卫**：迟到响应不比对 `consoleRunId`，会把旧 run 的行写入新 run
   终端并推进其 `seqRef`/状态。

修复：

- 新增 `pendingGapRef`（在途期间的「仍需补」置位）与 `consoleRunIdRef`（layout
  effect 同步当前 run）。
- `fillGap` 改为**循环消费**：in-flight 时只置位 pending，在途请求结束后若 pending
  置位则再取一次（从 `seqRef+1` 起，避免依赖被丢批次的起点）；结果落地前比对
  `requestRunId !== consoleRunIdRef.current` → 丢弃迟到响应。
- run 切换的既有 reset effect 一并清 `pendingGapRef`。

## Alternatives

- **在 `onMessage` 里缓存被丢批次的行**——放弃：实时批可能重叠/乱序，缓存再拼接
  会引入去重复杂度；服务端日志是权威序列，重取更简单且与 #1116 的语义一致。
- **用递归 `fillGap()` 自调用**——放弃：需要 `useCallback` 自引用（ref 转发），
  循环消费在同一异步函数内即可表达，且避免渲染期写 ref 触发 `react-hooks/refs`。
- **切 run 时 abort 在途请求（AbortController）**——放弃：`dedup.getRunLog` 未暴露
  signal 且改造成本大于收益；用 runId 守卫丢弃结果已消除错写。

## Verification

- `npx vitest run src/components/console/LiveConsole.test.tsx` → **4 passed**，新增：
  - `refills a gap batch that arrives while a fill is already in flight (#1278)`
  - `discards a stale gap-fill response after the run switches (#1278)`（断言迟到
    响应的 `status: 'SUCCESS'` 未污染新 run 的状态徽章）
- 反事实：把 `LiveConsole.tsx` 还原为 origin/main 后，两条新用例**均失败**；恢复后全绿。
- `npx tsc --noEmit` → 通过；`python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若回填请求将来改为「可取消 + 服务端游标」模式，`pendingGapRef` 的置位语义可并入
游标状态机；届时应同时补一条「run 切换时取消在途请求」的用例。
