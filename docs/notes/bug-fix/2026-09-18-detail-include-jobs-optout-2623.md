# PlanRun detail 支持 `include_jobs=false`（#2623 第 3 步 opt-out 先行）

Status: implemented
Class: bug-fix

## Decision

`GET /plan-runs/{id}` 的响应里**内嵌全量 jobs**，实测占 510-job run 的 **98.9%**
（193,866 B / 196,041 B），而仓内**零消费方**（`PlanRun` 前端类型未声明该字段；
`tools/site_config/agents.py` 已写明「Job 明细必须走专用端点」——内嵌 jobs 不带
step_traces，本就不可用于明细）。

本单按 owner 裁决走 **opt-out 先行**：

- 路由新增查询参数 `include_jobs: bool = True`；`false` 时 `build_plan_run_detail`
  **不查也不序列化** jobs（`jobs: []`）。**默认行为逐字不变**——仓外未知调用方不受影响；
- 前端两个调用点显式 opt-out：详情页 `usePlanRunDetailData`（不消费 jobs）与选机工作台
  `PlanExecutePage` 的重复检测（只读 `run_context.dispatch_device_ids`）。
  日志页在上一单（#2676）已改走 `/summary`，不再走 detail；
- 「默认不填充」属**对外契约变更**，另由 ADR 裁决——本单不做。

**一处被测试抓到的派生耦合**：`device_count` 原由内嵌 jobs 派生
（`_plan_run_out` 的 distinct-devices 语义）；直接置空 jobs 会让该字段**静默变 0**。
opt-out 分支因此用一条聚合查询（`SELECT DISTINCT device_id`）保住它——取 distinct
行而不是 `count(distinct …)`，因为集合语义把 NULL 也算一个值。

## Alternatives

- **直接默认不填充**（收益最大）：本轮不做。仓外调用方不可见，静默拿到空列表比报错
  更难排查；ADR 裁决后再切。
- **`?include_jobs=true` 默认关**：等价于上一条，同样待裁决。
- **删掉字段本身**：否决。字段是既有公开契约的一部分，删除是无法回退的破坏。
- **不动 detail，改让前端少拉**：做不到。首屏载荷由服务端响应模型决定，前端无法
  「只要一半」。

## Verification

- **反例构造（先证伪再采信）**：
  - 后端 ① 恒填充 jobs（忽略 `include_jobs`）→ 新用例 **FAILED**；② 保留分支但去掉
    `device_count` 聚合 → 同一用例 **FAILED**（「其余字段逐一不变」的断言抓的正是它）；
  - 前端 ③ 详情页调用点去掉 opt-out → `PlanRunDetailPage` 新用例 **FAILED**；
    ④ 工作台调用点去掉 opt-out → `PlanExecutePage` 既有断言 **FAILED**。
- 实测：`TESTING=1 … pytest backend/tests/api/test_plan_run_shape_1520.py -q` →
  **10 passed**；`… -k plan_run` → **195 passed**；
  `npx vitest run` → **128 files / 1036 tests passed**；
  `pytest tests/test_api_response_shape_contract.py -q` → **15 passed**（schema 未变，对拍仍绿）；
  `python scripts/run_gates.py check:quick` → **[OK] (12 gates)**。

## Revisit

- **「默认不填充」的 ADR**：需要先确认仓外调用方（脚本/外部集成）。证据面：
  `frontend/src` 零消费、`tools/` 零消费、`backend/tests` 只断言形状；若确认无外部
  依赖，可直接翻转默认值并把 `include_jobs=true` 留给诊断面。
- **列表端点同样有 81.4% 的无人消费体积**（`run_context` 51.1% + `plan_snapshot` 30.3%，
  issue #2623 的评论里已量化）——是否拆单/同法 opt-out，等那一条的裁决。
- **`device_count` 的语义**：本轮按「集合语义含 NULL」对齐；若将来把 NULL 设备视作
  异常数据（应为 0），聚合与派生两处要一起改，别只改一处。
