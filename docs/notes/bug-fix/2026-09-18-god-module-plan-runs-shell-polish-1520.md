# God-module：plan_runs 薄壳抛光 + 三次棘轮（#1520）

Status: implemented
Class: bug-fix

## Decision

路由已无业务 SQL；残余是重复装配、冗长 docstring 与分区注释。本刀零行为
变更收口：

1. `_manual_action_out` 合并 manual-retry/exit 响应装配；
2. `_raise_abort_http` / `_raise_dispatch_retry_http` 收口异常→HTTP 映射；
3. 压缩分区横幅与重复 docstring（真源仍在 ADR / service）；
4. 同 PR 棘轮：`plan_runs` 封顶 **622 → 507**（实测 482 × 1.05）。

`plan_runs.py` **596 → 482**。

## Alternatives

- **再抽 abort/retry 进 service**：弃——映射已是路由职责，再搬跳转成本 >
  行数收益；
- **只抛光不调棘轮**：弃——与门禁「同批下调」约定不符。

## Verification

- `test_plan_runs_api` + `test_plan_run_abort_api`（34 passed）；
- `ruff` + `check_god_files_ceiling` + `check:quick`。

## Revisit

- 三主战场均已薄壳且有棘轮；建议本 PR 合入后关闭 #1520；
- Issue 本 PR 仍 `Refs`，不自行 close。
