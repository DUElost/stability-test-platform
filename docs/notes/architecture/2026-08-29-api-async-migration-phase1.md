# 控制面 API async 迁移 — Phase 1（#517）

Status: in progress
Class: architecture

## 决定了什么

**Phase 1**（本批）：只读 admin 路由迁到 `get_async_db`，作为后续大路由迁移的模板。

| 路由模块 | 端点 | 状态 |
|----------|------|------|
| `audit.py` | `GET /api/v1/audit-logs` | ✅ async |
| `logs.py` | orphan 清单 | 待 Phase 2 |
| `plans.py` / `plan_runs.py` | 写路径 + 聚合 | Phase 3+（依赖 dispatcher 会话边界） |
| `agent_api.py` | 已是 async | — |

**规则**（新 PR 门禁）：

1. 新增只读路由默认 `AsyncSession` + `get_async_db`
2. 禁止在 sync `get_db` 事务内 `await` 调 async 派发
3. 业务逻辑留在 service 层；路由只做 session 适配

## 放弃的备选

- **一次性全量迁移**：拒绝。`plan_runs.py` 体量与 sync dispatcher 耦合过高，需分域。
- **heartbeat 改 native async**：拒绝本批。当前 `asyncio.to_thread` 包装 sync 体已满足并发；重写 `_process_heartbeat_with_db` 属独立 PR。

## 如何验证

- `python -m pytest backend/tests/api/test_audit.py -q`
- 合入前跑 PR backend-test（PG）

## 何时重议

- Phase 2：`logs.py` orphan + `mtbf.py` 只读
- Phase 3：`stats.py` / `results.py` 只读
- Phase 4：写路径与 `plan_dispatcher_sync` 边界（可能需 ADR 修订）
