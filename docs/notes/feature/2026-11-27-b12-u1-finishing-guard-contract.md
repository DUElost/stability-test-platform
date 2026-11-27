# B12 U1：统一 FinishingGuard 契约 / 加性 Schema / run 级 abort 时钟修复

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 2 底座单元 U1：

1. 新增共享契约 `backend/agent/contracts/finishing_guard.py`，以
   `kind ∈ {teardown_disarm, atomic_commit}` 统一有界护盾判据；遗留 B6
   `disarm_guard` 线协议字段完整兼容并吸收为 `teardown_disarm`。
2. 加性迁移 `b12u1a2b3c4d5`：`job_instance.finishing_guard` /
   `negotiated_capabilities`、`plan_step.atomic_group` /
   `skip_satisfies_delivery`、单行准入闸门表 `finishing_guard_admission`
   （`id=1`，初始 `admission_open=true`），含完整 `downgrade()`。
3. 能力常量 `finishing_guard_v2` / `atomic_commit` / `force_abort` 仅登记到
   `capabilities.py`，**不**并入 `AGENT_CAPABILITIES`（心跳上报留待 U5）。
4. 修复 `plan_run_abort` run 级重复 abort 覆盖首次 `at`/`deadline_at` 并清空
   `acknowledged_job_ids` 的 ADR-0043 D2 缺口；多主体强度 `max` / 时钟 `min`
   纯函数置于契约并由 `plan_run_context` 再导出。
5. `job_guard_deadline` 成为 recycler / abort reaper 统一护盾入口，预留
   `atomic_commit` 分支；`force=true` 与 `UNKNOWN` 候选纳入留待 U4。

## Alternatives

- 在 DB `status` 枚举新增 `FINISHING`：触及活跃索引与 reaper 全链路，ADR-0065
  已否决；本单元坚持投影模型。
- 把 B 类保护直接塞进 `disarm_guard` 特例：与 A/B 终态口径正交冲突，已否决。
- U1 即开启 Agent 心跳上报三向能力：半成品协商会让旧控制面误判，明确延后 U5。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_finishing_guard_u1_4206.py -q`
- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_finishing_job_deadline_u1_4206.py backend/tests/api/test_plan_run_abort_api.py -q -k "c1_ or finishing_guard or run_level_abort_clock or host_abort_clock"`
- 空库 `alembic upgrade head` → `downgrade -1` → `upgrade head` +
  `python -m backend.scripts.check_schema_sync`
- `./scripts/project_python.sh tools/dev/run_gates.py check:quick`

## Revisit

- U2 消费本契约做 `ui_status=finishing` 投影；U4 写入 `force` / 预登记激活与
  PG 交错测试；U5 开启能力心跳上报并实现 Agent 仲裁点。
- 若 schema-sync 基线因本迁移出现成对噪音，按既有基线流程人工确认后更新，
  不得静默扩大豁免。
