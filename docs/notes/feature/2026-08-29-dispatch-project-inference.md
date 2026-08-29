# 派发归属推断 + JIRA 键解析改读快照

Status: implemented
Class: feature

ADR-0029 项目域 P0 第一步（`feat/project-attribution-p0`）：让 plan_run 的
项目维度开始有真实数据，并统一 JIRA 键的归属口径。只写归属、不参与派发决策
（D5 挂起），推断/跨项目一律留痕 run_context。

## Decision

**1. PlanRun 归属冻结值解析（`backend/services/plan_dispatcher_sync.py`）**

`_prepare_queued_plan_run` 创建 PlanRun 时，`project_id` 不再直接取
`plan.project_id`，改经新函数 `_infer_frozen_project_id`：

- `plan.project_id` 非空 → 直接冻结（显式归属优先，行为不变）；
- NULL 时按目标设备的 `project_id` **众数**推断（`count DESC` 取首个）；
- 全部设备未归属 → `None`（无规则命中，显式「待归属」，不臆造——存量
  `test_plan_without_project_stays_null` 语义保持）；
- 目标跨项目 → 照常派发（admin 天然跨项目，不拒绝/不阻断/不校验同域），
  记 `logger.warning("plan_dispatch_project_mixed", ...)` +
  `run_context["project_mixed"] = [project_key...]`（sorted）；
- 推断命中 → `run_context["project_inferred"] = True`。

retry 路径（`precheck/runner.py:retry_plan_run_dispatch`）原地 re-queue 同一
PlanRun 行，不重建、不经过 prepare，冻结值保持不变——无需改动。

**2. JIRA 键解析链改读快照（`backend/api/routes/dedup.py`）**

`resolve_jira_project_key` 从 `PlanRun → Plan → test_project` 改为
`PlanRun.project_id → test_project` 直连：消除「同一页面两个归属口径」（页面
统计走 PlanRun.project_id，提单解析走 Plan.project_id 的矛盾）。与
`results.py:159-177` 的快照口径统一。`plan_run.project_id` 列已存在且
dispatcher 创建时冻结，本次只是让消费方对齐，省一次 Plan 查询。

缺键语义不变：返回 None 不阻断，WARNING 由调用方记。

## Alternatives

- **不推断、保持 NULL**：plan_run 项目维度继续全是 LEGACY/NULL，项目详情
  页永远 0 个 Run，项目级风险趋势（P2）无数据可聚合。放弃。
- **拒绝/阻断跨项目派发**：违背「归属错了应该改归属，不是拦派发」的定位；
  admin 天然跨项目。只留痕，不拦。
- **推断同时回填 device.project_id**：越界到 P1 规则表职责（P0 不改 schema、
  不动心跳热路径）；device 归属是人工/规则的事，派发只读。放弃。
- **resolve 从 jira_run 反查**：jira_run 表当前为空，且逐 run 解析仍是
  PlanRun → project 同源，直接读快照列最简。

## Verification

- 新增 `test_plan_dispatcher.py::TestDispatchProjectSnapshot`：
  `test_plan_without_project_infers_from_devices`（推断命中 + project_inferred）、
  `test_mixed_device_projects_keep_mode_and_mark_mixed`（众数 + mixed 留痕）、
  `test_explicit_plan_project_beats_device_inference`（plan 优先对抗 case）。
- 新增 `test_dedup_jira_endpoints.py::TestJiraProjectKeyWiring`：
  `test_snapshot_key_wins_over_current_plan_attachment`（Plan 事后改归属不影响
  历史 Run 的 JIRA 目标）；原种子改带 `run.project_id`（模拟 dispatcher 冻结）。
- 命令：`ALLOW_SQLITE_TESTS=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_plan_dispatcher.py
  backend/tests/api/test_dedup_jira_endpoints.py`（54 passed）；相邻套件
  precheck / admission_queue_step2 / plan_precheck / project_routes 共
  99 passed；`ruff check` 四文件全过。
- 生产影响待验证：新派发 Run 的 plan_run.project_id 将出现真实项目键
  （A57/V552AA），可查 `SELECT project_key, count(*) FROM plan_run ...` 对比
  96 LEGACY / 40 NULL / 1 V552AA 的存量分布。

## Revisit

P1 规则表（project_device_rule + project_pinned）落地后，推断依据应从
`device.project_id` 众数升级为「活跃规则解析」，或保持 device 事实（规则
已写回 device）不变——届时以规则表为准重议本函数。
