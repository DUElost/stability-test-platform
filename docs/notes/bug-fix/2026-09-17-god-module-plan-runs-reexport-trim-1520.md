# God-module 切片：plan_runs 删 re-export，测改 service 导入（#1520）

Status: implemented
Class: bug-fix

## Decision

路由里大段 `noqa: F401` re-export（devices / catalog / watcher / chain）只为
保测试从路由拿私有符号。本刀：

1. 三处测试改从真源 service 导入（`plan_run_devices` / `catalog` /
   `watcher_summary`）；
2. 路由删掉对应 re-export 块；仅保留端点实际用到的
   `_MAX_WATCHER_WINDOW_MIN`。

`plan_runs.py` **642 → 587**。

## Alternatives

- **继续留 re-export**：弃——god-file 体量虚高，且掩盖真源；
- **一并清 agent_api re-export**：弃——#2590 仍占窗，避免叠冲突。

## Verification

- `test_plan_run_dedup_key_2285` + `test_plan_run_stuck_alignment` +
  `test_plan_runs_api`（27 passed）；
- `ruff` + `check:quick`。

## Revisit

- `agent_api` re-export 同套路（等 #2590 合入后）；
- Issue #1520 保持 OPEN；`Refs #1520`。
