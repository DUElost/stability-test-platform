# audit-20260913 backend 批：jira-drafts 过滤下推 + rechain 自愈回填（#1696 #1717）

Status: implemented
Class: bug-fix

来源：2026-09-13 最近 24h 变更审计（窗口 `f268d455` 前 24h）。同批原含 #1698，
已被并行 Execution `fix-1698-retention-purge-jira`（PR #1724）领走，本批让出。

## Decision

1. **#1696 `GET /runs/jira-drafts` 草稿判据下推 SQL**（`backend/api/routes/runs.py`）：
   原实现按 `post_processed_at IS NOT NULL` 取 limit、Python 侧再 `if job.jira_draft_json`
   过滤。该「有时间戳无草稿」的行真实存在——post_completion 里草稿生成是
   try/except、时间戳无条件写，`refresh_report_cache_for_plan_run` 也只刷时间戳；
   先 LIMIT 后过滤会让这类行吃掉名额、静默少返回。改为
   `func.jsonb_typeof(jira_draft_json) == "object"`（列本就是 JSONB；SQL NULL
   与 JSON `null` 的 typeof 均非 `object`，一个判据同时排除两种无草稿形态），
   docstring 同步重写。

2. **#1717 rechain 后重放 dd44 回填**（`backend/alembic/versions/f6a5b4c3d2e1_…py`）：
   `8c4d5f47` 把 p9q8 rechain 到 dd44 之后，rechain 前把 p9q8 当 head 应用过的
   库（09-12 09:55Z–12:49Z 窗口）不会回溯执行 dd44——passthrough 三件套的
   `content_sha256` 永久保留 _lib 误写值。新 head 迁移以 `(name, version)` 键、
   `WHERE content_sha256 = :old` 重放同一回填：新链库 0 行更新、受损库自愈，
   免除逐库人工核对。迁移自包含，回填表与 dd44ee55ff66 逐字一致。
   **PR #1743 CI 修复（2026-09-13）**：merge `main` 后把 `down_revision` 从
   `x9y8z7a6b5c4` 重链到当时单 head `z7a6b5c4d3e2`（#1693 seed 链 tip），
   消除 `Multiple head revisions`；回填逻辑未改。

## Alternatives

- #1696 先取 limit 再扩窗补齐——否：分页语义变复杂，且 SQL 判据一行即可表达。
- #1696 保留 Python 过滤作双保险——否：与「判据单点」冲突，留双处判据将来必漂移。
- #1717 只在部署文档记录人工 UPDATE——否：自愈迁移成本低且消除「哪些库落在
  窗口内」的核对负担；幂等 UPDATE 对新链库无副作用。
- #1717 把 rechain 撤回恢复双 head——否：双 head 是被修的问题，方向倒退。

## Verification

- `pytest backend/tests/api/test_runs_jira_drafts_list.py -q`：**5 passed**
  （新增 `test_draft_less_rows_do_not_consume_limit`：SQL NULL 与 JSON null 两类
  无草稿行 + limit=1，修复前该用例返回空列表）；
- `pytest backend/tests/migration/test_reapply_dd44_backfill_after_rechain_1717.py -q`：
  **1 passed**（真 alembic 链 + 一次性 PG16：新链基线正确 → 模拟受损 → upgrade
  head 自愈 → 对照行（fill_storage v1.0.2 哨兵 sha）不被改写 → downgrade no-op →
  重复 upgrade 幂等；`DOWN_REVISION` 随 tip 同步为 `z7a6b5c4d3e2`）；
- 空库：`alembic heads` → 单 head `f6a5b4c3d2e1`；`alembic upgrade head` 成功
  （含 `z7a6b5c4d3e2 → f6a5b4c3d2e1`）；
- `python scripts/run_gates.py check:quick`：见 PR 记录。

## Revisit

- 受损库是否真实存在无法从仓库判定（依赖 09-12 当日部署时序）；本迁移落地后
  该问题失去意义（下次 `upgrade head` 自愈），无需运维核对。
- #1693 的 `y8z7…`/`z7a6…` 已合入 main；本 PR 已 merge-from-main 并把
  `f6a5…` 的 `down_revision` 重链到 `z7a6b5c4d3e2`。后续若 tip 再动，新迁移
  继续挂在当时单 head 之后，勿再硬编码旧 tip。
