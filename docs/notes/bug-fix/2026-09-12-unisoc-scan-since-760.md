# UnisocScanRunner 增量扫描缺 since 水位线（#760）

Status: implemented
Class: bug-fix

## Decision

`UnisocScanRunner.run_scan_result` 按 `mtime` 取最新 `*_org.xls`，无扫描启动
水位线。增量扫描复用同一 `plan_run_id` 时，本轮未产出新文件会把上轮报告当
本轮上送（与 MTK `ScanRunner` 已有 `scan_start` fresh 过滤不对齐）。

修复：在 `_run_log_scan_gt` 前记录 `scan_start = time.time()`，
`run_scan_result` 只接受 `mtime >= scan_start - 1` 的候选；无 fresh 则返回
`None`（日志 `unisoc_scan_result_no_fresh_org_xls`）。`run_scan_and_upload`
对非 `_org` 的 `Result_*.xls` 二次上送同样套用水位线。

## Alternatives

- 清空 scan_root 每轮：破坏 staging/硬链接复用；否决。
- 仅比文件名时间戳：工具命名不保证单调；否决。

## Verification

- `pytest backend/agent/tests/test_unisoc_scan_runner.py -q`
- `python3 scripts/run_gates.py check:quick`

## Revisit

若 scan_result 工具改写旧文件（同路径 in-place）导致 mtime 更新但内容陈旧，
需另加内容/世代标记；当前与 MTK 口径一致。
