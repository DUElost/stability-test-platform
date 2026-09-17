# main CI 红灯修复：PlanRunDetailPage 两例跟上 JOB_STATUS 合流接缝（#2441）

Status: implemented
Class: bug-fix

## Decision

**归因：`48992e35`（#2369）给 JOB_STATUS / PRECHECK_UPDATE 的 REST 失效加了 2s 合流
（`PLAN_RUN_SOCKET_COALESCE_MS`），而 `PlanRunDetailPage.test.tsx` 的三处断言仍按
「立即失效」写**——`waitFor` 默认 1s，等不到 2s 后才会发生的失效，于是本机与 main
全量 CI 同时红（backstop run 35148237108：`frontend-check` → Run vitest `2 failed |
939 passed`）。这正是「接缝改了用例没跟」那一类（与 #2372 记录同族）。

**修法是让用例跟上接缝，不是回退合流**：合流是 #2369 有意的降 thrash 措施
（ADR-0026 观测面跟进），语义正确。

具体三处：

1. 用例内引入 `COALESCE_WAIT = { timeout: PLAN_RUN_SOCKET_COALESCE_MS + 2_000 }`——
   **引用实现里的常量**而不是再写一个 2000，窗口将来调整时断言自动跟随；
2. 原「一个用例串跑 JOB_STATUS + WATCHER_SIGNAL + PRECHECK_UPDATE」拆成三条：
   串跑会有三段各 2s 的等待，触到 vitest 默认 5s 的**用例超时**（实测
   `Test timed out in 5000ms`）——拆开后每条只等一个窗口，失败点也自明；
3. 补一条**合流语义本身的断言**：连推两次 JOB_STATUS，`getDevices` 只被调用一次。
   #2369 只测了失效键列表（`planRunDetailUtils.test.ts`），没有任何用例覆盖「合流真的
   合并了」——去掉合流此前不会让任何用例变红。

## Alternatives

- **A. 回退 JOB_STATUS/PRECHECK 合流（让旧断言过）**：否决。合流是降 REST 风暴的有意
  设计（#2369 的 ADR-0026 跟进），回退等于用测试绑住实现退化。
- **B. 全文件改用假定时器 `vi.advanceTimersByTime`**：不做。该文件通篇实时钟 + 
  react-query 内部定时器，混用假定时器是新的不确定性来源；拆小用例已消除超时，收益
  不值这个风险。
- **C. 给原长用例加 `it(..., 20_000)` 超时**：可行（仓库无此先例），但保留了「一条用例
  串三条链」的耦合——失败时只知道「这条大用例红了」。拆分是同等成本下更好的形状。
- **D. 只把等待放宽、不加合流断言**：否决。那样「合流被删掉」仍然无人发现，等于这条
  修复只治了红灯、没锁住行为。

## Verification

- **红**：修复前本机与 CI 同为两例失败；CI 侧证据为 backstop run 35148237108 的
  `Run vitest` 步骤（失败用例名与该 job 的 `2 failed | 939 passed` 摘要）。
- **绿**：修复后 `npx vitest run`（全量）→ **118 files / 943 tests 全部通过**（本地
  Node 24；此前"本地环境差"的假设已被 CI 日志否证）。
- **变异检查（防摆设断言）**：把 `usePlanRunDetailData` 里 JOB_STATUS 的合流去掉
  （改为逐推即时失效）→ 新断言 `expect(mocks.getDevices).toHaveBeenCalledTimes(1)`
  立刻红（`1 failed | 25 passed`）；恢复实现后全绿。
- `npx tsc --noEmit`、`npx eslint --max-warnings 0`、`check:quick`（10 gates）通过。
- **未做**：未在浏览器验证合流的观感（本单只改测试；窗口语义由 #2369 的 ADR 跟进决定）。

## Revisit

- **我自己的归因失误（须记住）**：2026-09-16 我先把这两例判为「本机 Node 24 vs CI
  Node 22 的环境差、CI 是绿的」，并写进了记忆条目——错在拿 09-15 的 backstop 绿灯当
  本次基线，而回归发生在 09-16 合入之后。记忆已更正为「先看 main backstop 同一批用例
  的结论，再谈环境差」。这条教训比本单的代码改动更重要。
- **这类回归的暴露延迟**：`frontend-check`（跑 vitest）条件仍是
  `github.event_name != 'pull_request'`，PR 路径不跑前端测试 → 接缝改了用例没跟，要等
  每日 backstop 才红（本单就是隔了一天才被发现）。是否把 vitest 前移到 PR 路径是独立
  决策（时间预算 vs 覆盖），#2027 的 Revisit 也留了同一问。
- **`#2372` 族**：这类「改了接缝没改用例」的清单还包含后端侧（`backend/tests` 只在夜间
  跑）。若要做系统性收敛，应一并评估后端前移，而不是只补前端一条。
