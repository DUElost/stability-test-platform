# AI 助手 PR-C：PlanRun 运维写工具

**日期**：2026-08-31  
**关联**：ADR-0031 附录 phase-3、`dispatch_plan_run`（PR-B）

## 决定了什么

- 新增 T2b 写工具 5 个，均镜像 `plan_runs` REST 语义：`abort_plan_run`、`retry_plan_run_dispatch`、`manual_retry_job`、`manual_exit_job`、`trigger_plan_run_archive`。
- 实现集中在 `backend/services/ai_assistant/plan_run_ops.py`；`abort` / `retry_dispatch` 调用既有 service；`manual_*` 复用 `plan_runs` 路由内 `_load_job_in_run` 等 helper，审计动作与 API 一致（`patrol_manual_*`），details 增 `via: ai_assistant`。
- `trigger_plan_run_archive` 从 SAQ worker 经 `asyncio.run_coroutine_threadsafe` + `emit_agent_control` 桥接（与 `schedule_emit` 同模式），并写 `ai_assistant_trigger_plan_run_archive` 审计（API 本无审计）。
- 操作卡预览覆盖 abort / manual / retry / archive。

## 放弃的备选

- 把 `manual_*` 逻辑抽到 `services/patrol_manual.py` 再让路由与助手共用——正确但超出 PR-C 范围。
- `asyncio.run(emit_agent_control)` 在 worker 线程——会与主循环冲突，已拒绝。

## 如何验证

```bash
JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/services/test_ai_plan_run_ops.py \
  backend/tests/services/test_ai_dispatch.py \
  backend/tests/services/test_ai_tools.py -q
```

验收矩阵 #6（终态 abort → 409）：由 `abort_plan_run` 服务层保证，助手仅透传 `RuntimeError`。

## 何时重议

- API 收紧 abort 权限（附录开放问题 #1）时同步 `authz`。
- 归档若改为 `call_agent_control_sync` 等待 ack，需重评 UX 与超时。
