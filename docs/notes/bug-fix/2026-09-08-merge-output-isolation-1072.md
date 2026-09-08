# 并发 merge 共用 merge_result 串行化收割（#1072）

Status: implemented
Class: bug-fix

## Decision

工具 `start_log_scan` 固定写入 `{script_parent}/merge_result/{ts}/`，且
`find_fresh_merge_output_dir` 按「新目录 + max(mtime)」收割，无法绑定 PlanRun。
在控制面无法改工具输出契约的前提下，对
`snapshot → subprocess → find_fresh → publish → register` 使用
`merge_result/.stp_merge.lock` 的 `fcntl` 独占锁跨进程串行化（issue 允许的替代方案）。

涉及：`backend/services/dedup_scan.py`（`_exclusive_merge_tool_lock` +
`run_merge_sync`）；测试见 `test_dedup_scan_merge.py`。

## Alternatives

- 按 run/platform/round 隔离工具输出目录：需工具支持自定义 output，或
  复制/symlink 整棵工具树到临时 cwd；脆弱且部署耦合。
- 收割后按 xls 内容反查本轮 org 列表：可做增强，但解析成本高、格式漂移风险大。

## Verification

- `test_find_fresh_picks_newest_when_two_new_dirs`
- `test_exclusive_merge_lock_serializes_harvest`
- `test_run_merge_sync_holds_merge_lock_during_harvest`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_dedup_scan_merge.py -q`

## Revisit

若日后工具支持显式 `-merge_out`，改为 per-run 目录并去掉全局锁，以恢复并
发吞吐。
