# B12-U4：控制面 atomic_commit 握手、force abort 与 PG 交错测试

Status: implemented
Class: feature

## Decision

落地 ADR-0065 阶段 3 单元 U4（`#4206` v1.2）控制面侧：

1. 单行全局准入门禁 `finishing_guard_admission`（迁移 `b12u4a1b2c3d4`，`down_revision=b13u1c2d3e4f5`）与 `backend/services/finishing_guard.py` 两阶段握手（`pre_register` / `activate_permit` / `cancel` / 存活心跳）；锁序严格 `Job → PlanRun → admission FOR SHARE` 与 `Host FOR SHARE → Job → PlanRun`。
2. `POST /plan-runs/{id}/abort` 支持 `force` 单调合并、`UNKNOWN` 纳入中止候选；摘要字段 `already_terminal` / `force_requested_before_complete_settled` / `force_unsupported_hosts`；admin 关门与排空计数端点。
3. `flash_integrity` 欠账经 `_upsert_open_debt_sync` 在预登记开立；`cancel_pre_registration` 与 `close_flash_integrity_debts_sync` 仅关闭该 family（C8 不触碰 B6）。
4. Agent API finishing-guard 四端点（同步 Session + 显式 commit）；claim 冻结 `negotiated_capabilities`；recovery `ABORT_LOCAL` 透传 `force`；`build_plan_snapshot` 写入 `atomic_group` / `skip_satisfies_delivery`。
5. 集成测试 `test_finishing_atomic_pg_interleaving_u4_4206.py` 覆盖 C4–C8（文件名故意不含 `guard`）。

## Alternatives

- **预登记锁序 Host 先行**：ADR 方案 4 已否决，采用 Job → PlanRun → admission FOR SHARE。
- **force 仅 SocketIO 不传 run_context**：不满足 D5.8 重连恢复与审计，故 run/host 上下文与下发载荷均持久化 `force`。

## Verification

```bash
./scripts/project_python.sh scripts/run_pytest.py \
  backend/tests/integration/test_finishing_atomic_pg_interleaving_u4_4206.py \
  backend/tests/services/test_finishing_atomic_d_atomic_u4_4206.py \
  backend/tests/api/test_plan_run_shape_1520.py \
  backend/tests/api/test_plan_run_abort_api.py -q
./scripts/project_python.sh scripts/run_gates.py check:quick
```

## Revisit

- U5 Agent 仲裁与 `enter/grant` IPC、心跳三向能力上报；Agent 侧展开 VBUS `return_check` 后经 `atomic_group_frozen` 上报二层成员。
- `already_terminal` / `force_requested_before_complete_settled` 当前为 run 级布尔；若 U7 需要逐 Job 清单可再加 list 字段而不改布尔语义。
- CI 长跑下宿主心跳夹具必须用 per-call `_fresh_now()`（不得模块级冻结时间戳）。
