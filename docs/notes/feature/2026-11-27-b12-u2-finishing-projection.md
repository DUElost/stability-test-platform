# B12 U2：控制面 finishing 只读投影与契约 §5.1

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 单元 U2（`#4206` v1.1）：

1. 在 `plan_run_devices` 建立统一 `project_job_finishing` / `ui_status` /
   `job_exec_status` 谓词，复用 U1 `coerce_job_finishing_guard` 双列权威；
   持久化 `PlanRun.status` / `JobInstance.status` 仍为 `RUNNING`。
2. A 类：`EXECUTING_DISARM` 或有效 teardown 护盾，或无解除步骤的纯采集
   teardown → `finishing`；纯采集 `finishing_deadline_at` 恒为 `null`。
3. B 类：有效 `grant` 且 abort 覆盖后投影 `finishing`；有效 `grant` + 宿主心跳
   与 guard 存活新鲜时，预期 ADB 断开不得降为 `unknown`。
4. Minimap 保持精确 7 字段；仅扩展 `job_exec_status` / `facets.by_status` 含
   `"finishing"`；同 PR 修订契约 §5.1（ADR-0059 强度词）。
5. `GET /plan-runs?status=finishing` 按 PlanRun 级投影筛选，`total` 与 items
   同口径；`stats.running` 仍按持久化 `RUNNING`（D1.16）。
6. `host_upgrade_gate` 45s 排空预算不变；超时 `detail` / `abort_summary` 附加
   `finishing_kind`、`finishing_deadline_at`、`suggested_retry_window_seconds`。

## Alternatives

- 在 DB 枚举新增 `FINISHING`：与 ADR-0065 / §6 明确不做冲突，否决。
- Minimap 附带子类型字段：破坏契约 §5.1 七字段不变量，否决。
- 列表筛选用近似 SQL 而不做谓词求值：易与投影漂移，U2 选择精确谓词求值。

## Verification

- `scripts/run_pytest.py backend/tests/services/test_finishing_projection_u2_4206.py backend/tests/api/test_finishing_projection_api_u2_4206.py backend/tests/services/test_plan_run_devices.py -q`
- `scripts/run_pytest.py backend/tests/services/test_host_upgrade_gate.py backend/tests/api/test_plan_run_b2_3_minimap_pagination_job_83.py -q`
- `scripts/run_gates.py check:quick`

## Revisit

- U3 前端消费 `ui_status=finishing` 与列表筛选；U4 写入 `force` / admission 与
  B 类握手后，投影侧 deadline 与 trigger 覆盖面会更完整。
- HTTP `ABORT_DRAIN_TIMEOUT` 映射仍只转发 `lingering_jobs` + `abort_summary`；
  新字段已写入 `abort_summary` 以透出，若要顶层平铺需另开小改 `error_handlers` /
  `hosts`（本单元 scope 外）。
