# God-module：plan_runs 路由壳清理 + stall 测试去抖（#1520 / #2566）

Status: implemented
Class: bug-fix

## Decision

在 #2566 tip 上收口路由壳，并消掉挡住合入的 agent stall 时序 flake：

1. `plan_runs.py`：删未用 `_aware`/`_duration_seconds`；`_iso` 改为
   `plan_run_read_common.iso` 别名；压缩 C5a₂ 大段注释为两行索引。
2. `test_step_stall_detection`：`repeated_seq` / `regressing_seq` 在停写后
   `sleep(1.0)`，避免子进程先于 stall 窗口退出（本地 5 跑 3 红 → 8 跑全绿）。

`plan_runs.py` **709 → 652**（本刀约 -57）。

## Alternatives

- **只 empty-commit 重跑 CI**：弃——flake 本地可复现，应治本；
- **再开新 PR 叠清理**：弃——直接推进 #2566 更短路径。

## Verification

- stall 两用例连跑 8 次全绿；
- API：`test_read_api_auth` + `test_plan_runs_api`（86 passed）；
- `ruff` + `check:quick`（god-files plan_runs 652/2419）通过。

## Revisit

- `_require_plan_run` 仍可下沉 `plan_run_read_common`（多路由共用）；
- Issue #1520 保持 OPEN；`Refs #1520`。
