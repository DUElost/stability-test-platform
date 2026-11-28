# B12 U2：控制面 finishing 只读投影与契约 §5.1

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 单元 U2（`#4206` v1.2；PR #4218 第 1 轮复核返修 B1–B7）：

1. 在 `plan_run_devices` 建立统一 `project_job_finishing` / `ui_status` /
   `job_exec_status` 谓词，复用 U1 `coerce_job_finishing_guard` 双列权威；
   持久化 `PlanRun.status` / `JobInstance.status` 仍为 `RUNNING`。
2. A 类（B1）：按真实 teardown STARTED 足迹与版本角色判定纯采集；
   有足迹 + abort/exit 覆盖 + 待解除步骤且收据未到 → `finishing`；仅计划含
   采集 teardown、当下仍在 init 时不得因 abort 误标。
3. B 类（B2）：以 U1 `job_guard_deadline(read_only=True)` 为底座，并核对宿主
   `ONLINE` / 心跳新鲜 / guard 存活 / 未超 `D_atomic` / 非 `force=true`；
   失能时不得维持 `finishing` 或豁免 ADB 断连降级。
4. 列表（B3）：`status` 多选按投影与持久化并集；非法值恢复 422。
5. 升级门禁（B4）：504 `finishing_kind` 与 `finishing_deadline_at` 同源配对；
   409 `HOST_ABORT_PENDING` 透传收尾提示。
6. 读侧 `capabilities.manual_retry`（B5）与写侧断连 409 同源。
7. 同 PR 同步 `frontend/src/utils/api/types.ts` 可选字段（B6）；契约 §5.1 第 4
   条拆成一句一规则（B7）。
8. Minimap 保持精确 7 字段；仅扩展词表含 `"finishing"`。

## Alternatives

- 在 DB 枚举新增 `FINISHING`：与 ADR-0065 冲突，否决。
- Minimap 附带子类型字段：破坏契约 §5.1，否决。
- U2 推迟 types.ts：与 `test_model_fields_are_declared_in_ts` 冲突，v1.2 否决。

## Verification

- `scripts/run_pytest.py backend/tests/services/test_finishing_projection_u2_4206.py backend/tests/api/test_finishing_projection_api_u2_4206.py backend/tests/services/test_plan_run_devices.py backend/tests/services/test_host_upgrade_gate.py backend/tests/api/test_plan_runs_api.py tests/test_api_response_shape_contract.py -q`
- 独立复核夹具（B1–B5）
- `scripts/run_gates.py check:quick`

## Revisit

- U3 前端消费 `ui_status=finishing` 与列表筛选；U4 写入 `force` / admission 与
  B 类握手后，投影侧覆盖面会更完整。
