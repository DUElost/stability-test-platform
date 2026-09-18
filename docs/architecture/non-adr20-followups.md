# Non-ADR20 Followups

This note tracks architecture cleanup that is still intentionally separate from
the ADR-0020 Plan/PlanStep migration. It is an active debt note, not an
implementation spec.

## Route Split Boundary

**Tracking:** [#60](https://github.com/DUElost/stability-test-platform/issues/60)
（**Closed 2026-09-19** — superseded，非按原文四路由文件拆分）。

**Status (2026-09-19):** 原痛点是 `agent_api.py` 三千行级胖路由（claims /
runtime / ingest / control 混文件）。该成本前提已消失：路由现约 **391 行 /
19 薄壳**；领域逻辑由 [#1520](https://github.com/DUElost/stability-test-platform/issues/1520)
垂直下沉到 `backend/services/agent_*.py`（约 19 模块）；共享 schema 落在
`backend/api/schemas/agent.py`（#84 落点之争由既成事实回答）；防回流由
`tools/dev/check_god_files_ceiling.py`（`agent_api.py` 封顶 411，棘轮只许下调）
承担。

原文建议的 `agent_claims.py` / `agent_runtime.py` / `agent_ingest.py` /
`agent_control.py` **未落地、也不再作为必做项**——可读性四文件拆分若将来要做，
另开可验收的新单即可。`/agent/...` URL 与契约未因本债变更。

历史目标模块对照（归档，勿再当待办）：

| Target module (historical) | Handlers (path → function) |
|---|---|
| `agent_claims.py` | `POST /jobs/claim` → `claim_jobs`; `GET /jobs/pending` → `get_pending_jobs`; `POST /recovery/sync` → `recovery_sync` |
| `agent_runtime.py` | heartbeat / complete / status / extend_lock / extend-batch / step status / patrol-heartbeat / coordinator-heartbeat |
| `agent_ingest.py` | `POST /steps`；`POST /log-signals`；`POST /jobs/{id}/artifacts` |
| `agent_control.py` | `GET /{host_id}/archive-status`（及未来 backpressure / 控制面专用端点） |

## Response Envelope Order

Do not convert all responses in one pass. Migrate by external surface:

1. Agent runtime endpoints, because Agent retry logic depends on status codes.
2. Admin/user-facing execution APIs.
3. Deprecated compatibility routes, with explicit deprecation headers.

Each migration step should include a contract test for both success and error
shape.

## Grep Checks

```bash
rg -n 'response_model=.*ApiResponse|return ok\(|return err\(' backend/api/routes
rg -n 'HTTPException\(|detail=\{|detail=\[' backend/api/routes
rg -n '@router\.(get|post|put|patch|delete)' backend/api/routes/agent_api.py
```

P2（Response Envelope）仍 deferred；与已关闭的 #60 路由归属债解耦——envelope
迁移另择机，不必再等四路由文件拆分。
