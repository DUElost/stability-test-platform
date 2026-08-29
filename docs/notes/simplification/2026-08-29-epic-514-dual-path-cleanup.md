# Epic #514 双轨收口第二批（#515–#523）

Status: implemented
Class: simplification

## Decision

按 2026-08-29 复核结论逐项删除仍并存的兼容路径（父 Epic #514）：

| Issue | 改动 |
|-------|------|
| #516 | 删 `active_unbound_mtbf_run_ids` 与 export `force` / `ACTIVE_MTBF_RUNS` |
| #518 | 控制面 dedup scan 仅认 `STP_BACKEND_DEDUP_SCAN_*` |
| #520 | 前端 `isJobStuck` 仅信 `is_stuck` / `heartbeat_deadline_at` |
| #523 | SocketIO `on_step_log` 仅接受 `lines` 批量 |
| #522 | 删 `GET /jobs/pending` compat 路由（非 410） |
| #521 | `OperationScheduler` 缺失时 fail-fast（`operation_scheduler_required`） |
| #515 | watchdog 不再做 UNKNOWN grace→FAILED；归 reconciler |
| #519 | 风险评级改读 DLE + 未链接 signal（`log_observation.py`）；watcher-summary 仍读 signal |
| #517 | Phase 1：`audit.py` 迁 async；迁移矩阵见 architecture note |

## 放弃的备选

- **#519 一次性删 `job_log_signal`**：拒绝。watcher-summary / 风险评级仍读 signal；需单独 ADR 级 UI 迁移 PR。
- **#522 删 legacy AEE Plan 404 守卫**：拒绝。存量 DB 行可能仍在；仅删 410 路由。
- **watchdog grace 与 reconciler 双跑**：拒绝。#515 后 reconciler 独占 grace 终态。

## 如何验证

- `python -m pytest backend/tests/services/test_log_observation.py backend/tests/api/test_audit.py -q`
- `cd frontend && npx vitest run src/hooks/plan-run/planRunDetailUtils.test.ts`
- agent tests 全量（pipeline_engine scheduler 分支）

## 何时重议

- watcher-summary 改读 DLE（#519 剩余 UI 面）
- #517 Phase 2+ 路由清单见 `docs/notes/architecture/2026-08-29-api-async-migration-phase1.md`
