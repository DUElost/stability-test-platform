# plan_step.step_key 长度统一——保存入口 422 收口（#938）

Status: implemented
Class: bug-fix

## Decision

#938（R03-F05）：`plan_step.step_key` 为 `String(256)`，原样成为下发 `step_id`
（`plan_dispatcher_core.py`），回传写入 `step_trace.step_id VARCHAR(128)` 时
129–256 字符失败并回滚同批事务——合法保存的 Plan 无法持久化步骤结果。

裁定采用**源头 422 拒绝**（而非全链路加长）：`PlanStepIn.step_key` 收紧为
`Field(min_length=1, max_length=128)`，与下游 `step_trace.step_id` 上限对齐；
`PlanCreate` / `PlanUpdate.steps` / `PlanChainTailCreate.steps` 三路共用
`PlanStepIn`，单点收紧全覆盖。`min_length=1` 顺带把「Pipeline schema 仅限非
空」的校验前移到保存入口。零迁移、零运行时其他改动。

存量安全依据（生产库只读探查，2026-09-08）：`plan_step` 136 行、step_key
最长 20 字符、`>128` 为 0 行；`step_trace.step_id >128` 亦 0 行——收紧不
影响任何存量 Plan。

## Alternatives

- 全链路统一加长（step_trace.step_id → 256）：需 alembic 迁移改列宽且放宽
  回传边界——只为容纳从未出现过的 129+ step_key；源头上限 128 与现有表结构
  天然闭合，成本更低。
- 回传侧截断：issue 明文禁止——截断破坏标识可追溯性。
- 仅加 Pipeline schema 校验：Pipeline 校验在执行面，保存面仍放行，问题只
  是推迟到下发后才暴露。

## Verification

- `pytest backend/tests/api/test_plans_api.py`：60 passed（一次性 PG16 容器
  跑真库；新增 3 态：恰 128 保存成功 / 129 拒 422 / 空 key 拒 422）；
- 存量只读探查见上（0 行受影响）；
- ruff、gov-surface S1–S12 全绿；
- Registry：fix-938-step-key-length 全程登记（--issue 938）。

## Revisit

- 若未来某脚本确需长 step_key（当前最长 20），届时再评估全链路加长迁移；
- #778 链尾路径与 #882 局部更新语义的同源契约漂移由各自单跟踪（本单只覆盖
  长度面）。
