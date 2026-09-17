# God-module：plan_runs 壳清理 + stall 去抖（补合 #2566 漏提交）（#1520）

Status: implemented
Class: bug-fix

## Decision

#2566 合入时漏掉 tip 上的 stall/壳清理提交；本 PR 从 main 重放并加一刀：

1. stall：`repeated_seq` / `regressing_seq` 停写后 `sleep(1.0)` 去抖；
2. 路由壳：删未用 `_aware`/`_duration_seconds`，`_iso` → `read_common.iso`，
   压缩 C5a₂ 注释；
3. `_require_plan_run` 下沉 `plan_run_read_common.require_plan_run`；export 复用。

`plan_runs.py` **709 → 642**。

## Alternatives

- **只 empty-commit 重跑已合入 PR**：不可行——提交未进 merge；
- **_require_plan_run 留路由**：弃——多端点共用，应收口。

## Verification

- stall 两用例 + API smoke（88 passed）；
- `ruff` + `check:quick`（god-files plan_runs 642/2419）。

## Revisit

- `plan_runs` 已基本薄壳；后续若还有体量，优先 agent god-file 或注释/re-export；
- Issue #1520 保持 OPEN；`Refs #1520`。
