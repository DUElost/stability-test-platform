# #2187 扩面：plans.py opt-in 形状契约，当场钉出一个幽灵返回类型

Status: implemented
Class: bug-fix

## Decision

把 `backend/api/routes/plans.py` 纳入响应形状契约的 opt-in 文件集
（`test_api_response_shape_contract` 的 I-9 台账机制——#2187 主诉「对拍覆盖不到
`ok({...})` 端点」收口后，剩余面按文件逐批推进）：

- 两个 typed 模型登记轴线 C 对拍：`PlanOut ↔ Plan`（18↔18 零漂移）、
  `PlanRunSummaryOut ↔ PlanRunTriggerResult`（新 TS interface，15↔15）；
- `delete_plan` / `preview_plan_run` 两处运行期 dict 按台账认领（不冒充已收口）。

**登记当场逮到的真东西**：`api.plans.run()` 的返回类型一直标 `PlanRun`——
那是 plan-runs 列表/详情投影，比触发端点实际返回的 `PlanRunSummaryOut`
**多 9 个键**（capabilities/jobs/device_count/enqueued_at/queue_reason/
priority/plan_name/project_key/next_admission_at）。按 #787 的口径这就是
幽灵声明：消费方若读 `run.capabilities.abort` 会拿到 undefined 且类型系统
不会拦。全仓唯一调用点（PlanExecutePage，确认后仅读 `run.id`）碰巧没踩——
**「没出事」不是「类型对」**，这正是契约门禁存在的理由。修法：新增
`PlanRunTriggerResult`（逐字段复刻后端模型）并收紧 client 返回类型；
wire 形状零改动，纯类型事实纠正。命名注释同时钉住它与
`GET /plan-runs/{id}/summary` 的 `PlanRunSummary`（名字近、形状远）防混用。

## Alternatives

- **`run()` 保留 `PlanRun` 标注 + 加注释**：弃——幽灵键声明留着，下一个读
  `.capabilities` 的人仍静默中招；
- **把 `PlanRun` 的 9 个多出的键改成可选**：弃——那会连带放松 plan-runs
  面（detail 端点真返回它们）的类型，为一个端点污染另一个面的声明。

## Verification

- 契约 15 passed（新配对双向对拍当场通过）；
- `test_plans_api.py + test_read_api_auth.py` → **145 passed**；
- vitest `PlanExecutePage.test.tsx` → **58 passed**（唯一消费点行为回归）；
- `check:quick` 12 门禁绿（局部 import 棘轮 610 未涨）。

## Revisit

- 下一批候选：`projects.py`（15 typed 端点、1 dict）、`scripts.py`（9、2 dict）；
  `agent_api.py`（16、10 dict）等 cursor 的 #1520 切片退场后再动，别撞在飞的
  搬迁面；
- `/specialties` 返回 `ApiResponse[List[dict]]`：内层无具名模型、现判据扫不到——
  要么升 `SpecialtyOut` 要么给解析器补「list[dict] 内层认领」记法，挂此台账不隐身。
