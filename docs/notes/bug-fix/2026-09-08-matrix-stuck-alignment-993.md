# R06-F08 落地：矩阵 stuck 判据与 recycler liveness 对齐（#993）

Status: implemented
Class: bug-fix

## Decision

设备矩阵 API 的 `heartbeat_deadline_at` 以 `job.updated_at` 为主候选计算：
合法等待执行槽位的 Job（`WAITING_EXECUTION_SLOT` / `PATROL_SLEEP` /
`WAITING_BARRIER`）不产生执行心跳，updated_at 停留在等待开始时刻——
即使 Coordinator 持续存活也被标 `is_stuck=true` 误判；recycler
（回收判死唯一权威）自 #288 起明确不以 updated_at 为存活信号。

修复（`backend/api/routes/plan_runs.py`）：按 recycler
`_running_liveness_anchor`（ADR-0026 §3）重写 `_running_heartbeat_deadline`：

- `EXECUTING_STEP` + 执行心跳 → 执行心跳时钟（graded timeout 带 patrol
  multiplier，与 recycler 同参）；
- `WAITING_*` + per-host coordinator 心跳 → coordinator 时钟
  （`COORDINATOR_HEARTBEAT_TIMEOUT_SECONDS`，等待合法 invariant ②——只有
  coordinator 死亡才判卡）；
- 信号缺失（含 execution_state 未知）→ dispatch 锚（started/created，
  **不 consult updated_at**）；
- 组装处一次查询本 run 的 `PlanRunHost.coordinator_heartbeat_at` 传入
  （run 级行数 = host 数，量级可忽略）。

`_WAITING_EXECUTION_STATES` 集合与 coordinator 超时常量在 plan_runs 本地
定义并与 recycler 互注同步（recycler import 链重且含 socketio 依赖，不跨
层 import——两处同步义务见 Revisit）。

## Alternatives

- **API 直接 import recycler._running_liveness_anchor 复用**——放弃：
  recycler 顶层 import 链含 `socketio_server`/`lease_manager` 等重依赖，
  API→scheduler 引入循环 import 风险；纯函数语义已注释对齐；
- **常量/等待集合上移共享模块**——放弃：牵动 recycler 既有 import 面与
  测试，收益（去重复）与改动面不匹配；现阶段互注 + Revisit 承担同步义务；
- **只修 WAITING_* 分支保留 updated_at 主路径**——放弃：非对齐的残余
  分支（unknown 态继续被续租刷新）正是 #288 已裁决禁止的语义。

## Verification

- **反例实证**：回退 plan_runs 保留测试 → 新文件收集失败（对齐常量不
  存在 = 旧实现无对齐语义）；修复版全绿；
- 新增用例（`test_plan_run_stuck_alignment.py` 7 例）：非 RUNNING 无
  deadline / EXECUTING_STEP 心跳与缺省锚 / **WAITING_* + coordinator 新鲜
  不 stuck**（核心回归，updated_at 10 分钟前仍不误判）/ coordinator 死亡
  老化 / coord 行缺失锚 dispatch / unknown 态锚；
- `test_plan_run_aggregation_endpoints.py` 矩阵端点全套回归 **64 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- **双向同步义务**：`_running_heartbeat_deadline` 与
  `recycler._running_liveness_anchor` 的任何分支/超时改动须同步另一处
  （两文件注释互指）；若未来出现第三处消费，应将集合与常量上移共享
  模块（当时机出现时顺带清理 recycler import 链）；
- 矩阵端点其它 stuck 衍生展示（grace_remaining_seconds 等）不涉本判据，
  未改动；`last_patrol_heartbeat_at` 仍作展示字段（`last_heartbeat_at`）。
