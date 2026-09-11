# PlanRun 成败语义边界文档化（#815）

Status: implemented
Class: bug-fix

## Decision

背景（#815，四域审查疑似 A）：sleep/gpu/powercycle finish 脚本测试自判 FAIL
（`final_status=FAIL` / `failed_rounds>0`）时仍 `output_result(True)`。issue 假设
"若 PlanRun 成败由 step success 聚合，则整链误报绿"，修复方向标注"**需先裁定
聚合口径**"。

**口径核查**（本次读代码确证）：

- PlanRun 聚合（`plan_run_aggregation.py:_resolve_plan_run_status`）只消费 **Job
  终态计数**（FAILED/ABORTED 数 + failure_threshold）；
- Job 终态由 lifecycle `termination_reason` 决定（`pipeline_engine.py:1868`：
  `completed`/`timeout` → COMPLETED；`abort`/`manual_exit` → ABORTED；其余 →
  FAILED）；
- **teardown step 的 success 不参与** Job 终态（`:2240-2290` 只把
  `teardown_status` 记入 metadata）；finish 脚本的自判结论落在 metrics /
  `test_case_result`（sleep_finish 写 detail 文件即该结果源）。

→ issue 的疑似前提**不成立**：三层语义自洽——脚本 `output_result(True)` 表示
"收取执行成功"（与其执行语义一致），测试结论在结果层呈现。

**裁定**（2026-09-11 用户裁决）：**文档化语义**——

- `docs/design/07-execution-protocol.md` §2 新增「成败语义（#815）」：PlanRun 绿
  = 执行链跑通 ≠ 测试通过；测试结论消费 metrics / `test_case_result`；INCOMPLETE
  「收取即成功」为同一设计的有意边界；
- 无代码改动（不改 `output_result`、不动聚合链）。

## Alternatives

- **teardown step 自判 FAIL 时改 `output_result(False)`**——放弃（本批）：step
  报告将与其执行语义（收取成功）冲突，产生"步骤 FAILED + 运行 SUCCESS"组合需额外
  解释；观测价值已由 metrics 层覆盖；
- **协议变更：测试 FAIL → PlanRun FAILED**——放弃：需设计 step/teardown 失败 →
  `termination_reason` 的升级规则并覆盖 INCOMPLETE 有意边界，跨协议层需 ADR；
- **维持隐含语义、不文档化**——放弃：issue 明确"需裁定"，不落文档则"绿"的误读
  长期存在。

## Verification

实际运行（worktree `/tmp/stp-815`，基于 `origin/main`）：

- `check:quick` → 7 gates 全绿；
- 口径证据（本次核查）：`plan_run_aggregation.py:_resolve_plan_run_status`（Job
  计数 + 阈值）、`pipeline_engine.py:1868`（termination_reason → mq_status）、
  `pipeline_engine.py:2240-2290`（teardown 仅记 metadata）、
  `sleep_finish/v1.0.1/sleep_finish.py`（detail 落结果层）；
- 文档落点：`07-execution-protocol.md` §2 PlanRun 状态机章。

未完成（pending）：

- 纯文档改动、无真机验证项；若未来选择协议变更（见 Revisit），需先 ADR。

## Revisit

- 若产品要求"测试 FAIL 即 PlanRun FAILED"，先走 ADR 修订执行协议 §2 语义，再改
  聚合链；
- 若结果层（`test_case_result`）消费面变化，同步本文档的语义描述。
