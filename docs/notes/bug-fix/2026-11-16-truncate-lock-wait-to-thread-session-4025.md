# 修复 `asyncio.to_thread` 跨线程传 Session 泄漏事务与 `TRUNCATE` 清库无限锁等（#4025）

Status: implemented
Class: bug-fix

载体 issue：#4025（由 PR #4022 `pr-backend-tests-informational` Run `43791919020` / Job `133052064606` 在 `77%` `test_r523_backflow_meets_all_acceptance_lines` 之后卡死满 90 分钟暴露）。

## Decision

针对「异步协程取消后仍继续使用外层 `Session` 的 `to_thread` 线程遗留 `idle in transaction` 锁，进而导致下一测试 `TRUNCATE TABLE` 在无 `lock_timeout` 下无限阻塞」的复合故障，在三层实施最小根因收敛：

1. **隔离 `asyncio.to_thread` 的 `Session` 所有权**
   - [`backend/api/routes/heartbeat.py`](../../../backend/api/routes/heartbeat.py)：`heartbeat_route` 不再在事件循环上注入 `Depends(get_db)` 并跨线程传递给 `_process_heartbeat_with_db`，改为在 worker 线程内的 `_process_heartbeat_in_thread` 中自开自关 `SessionLocal()`（若 `app.dependency_overrides` 覆写了 `get_db`，则在 worker 线程内消费并关闭该覆写生成器以兼容测试夹具）；同时保留 `heartbeat(payload, db)` 函数签名以兼容直接调用该协程函数的既有测试。
   - [`backend/services/precheck/runner.py`](../../../backend/services/precheck/runner.py)：收口全仓同类问题。`drive_dispatch_gate` 在 `own_session=True`（自建 `SessionLocal()`）时，通过 `_run_sync_with_db` 先释放外层只读事务并在 worker 线程内独立开/关 `SessionLocal()` 后再调用 `precheck_sync._push_mismatched_scripts`、`precheck_sync._sync_host_via_hot_update` 与 `complete_plan_run_dispatch`（外部显式传入测试 `db` 时保持既有共享语义）。

2. **停止压测探针的硬取消**
   - [`backend/tests/services/test_plan_run_abort_backflow_scale_3243.py`](../../../backend/tests/services/test_plan_run_abort_backflow_scale_3243.py)：`_drive_backflow` 中的 `_probe_loop` 由 `probe_task.cancel()` 硬打断正在进行的 ASGI `/api/v1/heartbeat` 请求，改为通过 `asyncio.Event` 通知退出并优雅等待 `probe_task` 收尾（带 10s 上界兜底），避免在飞线程池任务溢流到下一用例。

3. **测试清库有界锁超时与持锁现场取证**
   - [`backend/tests/conftest.py`](../../../backend/tests/conftest.py)：`_truncate_all_tables` 在 PostgreSQL 上执行 `SET LOCAL lock_timeout = '15s'`，将单向等锁从 90 分钟挂死收敛为快速失败；并在 `DeadlockDetected` 与 `LockNotAvailable`（`lock timeout`）两类锁冲突上均调用 `_dump_deadlock_scene`。同时扩展 `_dump_deadlock_scene` 的 `pg_locks` 查询以包含当前库普通表的 `relation` 级已授予锁，确保 `idle in transaction` 持锁方可直接在日志定位。
   - [`backend/tests/test_truncate_quiescence.py`](../../../backend/tests/test_truncate_quiescence.py) 与 [`backend/tests/api/test_to_thread_session_ownership_4025.py`](../../../backend/tests/api/test_to_thread_session_ownership_4025.py)：补充死锁/锁超时序列测试、真实 `QueuePool` 取消不泄漏回归测试与真实 PostgreSQL `idle in transaction` 触发 `lock_timeout` + `TRUNCATE_DEADLOCK_SCENE` 取证测试。

## Alternatives

- **仅在 `heartbeat_route` 中使用 `asyncio.shield` 包裹 `to_thread`**：`asyncio.shield` 只阻止外部取消传播到内层 future，但外层路由协程仍会在 `await` 处抛出 `CancelledError` 并触发 FastAPI 依赖清理提前执行 `get_db()` 的 `db.close()`，无法消除工作线程继续复用已关闭 `Session` 的竞态；让 worker 线程自持 `SessionLocal()` 生命周期是唯一根本解。
- **把 `_truncate_all_tables` 的 `lock_timeout` 设在连接级或改为重试**：重试只会掩盖泄漏源且放大墙钟抖动（见 `#2074`）；`SET LOCAL lock_timeout` 仅作用于清库单事务且失败即刻取证上抛，不影响业务测试自身锁语义。

## Verification

在隔离 worktree `.wt/fix-4025-truncate-lock-wait`（分支 `fix/4025-truncate-lock-wait-and-to-thread-session`，`unset TEST_DATABASE_URL` 走 testcontainers PostgreSQL）完成以下验证：

- 新增与受影响后端测试（经 `scripts/run_pytest.py`）：
  - `backend/tests/test_truncate_quiescence.py` + `backend/tests/api/test_to_thread_session_ownership_4025.py` + `backend/tests/api/test_heartbeat.py` + `backend/tests/services/test_plan_precheck.py`：`69 passed`（22.61s）；
  - `backend/tests/services/test_plan_run_abort_backflow_scale_3243.py` + `backend/tests/api/test_to_thread_session_ownership_4025.py`：`12 passed`（12.52s）；
  - `backend/tests/api/test_agent_dual_write.py -k heartbeat`：`7 passed, 68 deselected`（2.50s）。
- 仓库级离线子集 `repo-tests`（`tests/`）：`2537 passed, 18 skipped`（678.02s）。
- `python:3.11-slim` 容器 `python -m compileall -q backend tools scripts tests` 通过；`scripts/run_gates.py` 17 项非前端门禁（`schema-at-head` 因未配 `DATABASE_URL` WARN 跳过、`env-inventory`、`ruff`、`compileall`、`layering`、`orphan-models`、`gov-surface`、`ai-work`、`god-files`、`inner-imports`、`tool-contract-fixture`、`tool-manifest`、`transitions`、`pollution`、`ip-leak`、`prom-alerts`（95 passed）、`agent-tests-collect`（3427 collected））通过；worktree 无 `frontend/node_modules` 未跑前端门禁，且未修改 `frontend/`。`agent-tests` 跑录 `3424 passed, 2 skipped, 1 failed`（`backend/agent/tests/test_flash_preflight_v102.py::test_dialout_absent_and_no_rule_fails` 属本机环境前置，与本 PR 无关）。完整 CI checks 交由 PR required checks 验证。

## Revisit

- 本轮仅收口静态扫描定位到的两处「`Session` 跨 `asyncio.to_thread` 传递」点（`heartbeat.py` 与 `precheck/runner.py`）及测试清库超时；若后续 `run_gates.py` / CI 引入基于 AST 的「禁止向 `asyncio.to_thread` 传 `Session`」静态守卫，可进一步阻断同类模式回归。
- CI job 的 pytest 全局超时（如 `--timeout` / `faulthandler`）涉及全套 `backend/tests/` 运行时口径与 `#3576` 墙钟观测，未在本 PR 顺手改动，留待单独评估。
