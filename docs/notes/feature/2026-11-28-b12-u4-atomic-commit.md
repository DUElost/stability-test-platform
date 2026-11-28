# B12-U4：控制面 atomic_commit 握手、force abort 与 PG 交错测试

## Decision

落地 ADR-0065 阶段 3 单元 U4（`#4206` v1.2）控制面侧：

1. 单行全局准入门禁 `finishing_guard_admission`（迁移 `b12u4a1b2c3d4`，`down_revision=b13u1c2d3e4f5`）与 `backend/services/finishing_guard.py` 两阶段握手（`pre_register` / `activate_permit` / `cancel` / 存活心跳），锁序严格遵循 ADR-0065。
2. `POST /plan-runs/{id}/abort` 支持 `force` 单调合并、`UNKNOWN` 纳入中止候选，摘要字段 `already_terminal` / `force_requested_before_complete_settled` / `force_unsupported_hosts`。
3. `flash_integrity` 欠账经 `_upsert_open_debt_sync` 在预登记开立；`close_flash_integrity_debts_sync` 仅关闭该 family（C8）。
4. Agent API 注册 finishing-guard 端点；claim 冻结 `negotiated_capabilities`；recovery `ABORT_LOCAL` 透传 `force`。
5. 集成测试 `test_finishing_atomic_pg_interleaving_u4_4206.py` 覆盖 C4–C6/C8 与 activate 烟测。

## Alternatives

- **预登记锁序 Host 先行**：ADR 方案 4 已否决，采用 Job → PlanRun → admission FOR SHARE。
- **force 仅 SocketIO 不传 run_context**：不满足 D5.8 重连恢复与审计，故 run/host 上下文与下发载荷均持久化 `force`。

## Verification

```bash
./scripts/project_python.sh scripts/run_pytest.py backend/tests/integration/test_finishing_atomic_pg_interleaving_u4_4206.py -q
./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_plan_run_abort_api.py backend/agent/tests/test_finishing_guard_u1_4206.py -q
```

（待本地 PG `TEST_DATABASE_URL` 可用时执行；空库迁移含 `b12u4a1b2c3d4` downgrade/upgrade。）

## Revisit

- U5 Agent 仲裁与 `enter/grant` IPC 未在本单元；心跳能力上报仍待 U5。
- `plan_dispatcher_core` 未物化 `atomic_group` 快照校验，预登记由 Agent 提交 `atomic_group_frozen` 列表，控制面只做预算与门禁校验（最小 U4 口径）。
- C5/C7 完整 watchdog×activate 与 abort×complete 交错用例可在 U5 合入后补强线程屏障。