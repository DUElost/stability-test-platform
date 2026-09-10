# scan staging 回收改为异常安全（#1277）

Status: implemented
Class: bug-fix

## Decision

`ScanRunner.run_scan_and_upload`（`backend/agent/scan_runner.py`）的 staging 回收
原先排在 upload 成功之后、且无 `try/finally`：`upload_scan_report` 抛异常时
`.stp-scan/pr<plan>-*`（HDD 事件文件的硬链接目录）不会回收，占盘到该 plan_run
下一轮 scan 的 `_prepare_scan_root` 覆盖才释放——长时不重跑即持续占盘。

修复：把「upload org/dedup 产物」包进 `try`，`finally` 中无条件
`reclaim_scan_staging(self._last_scan_root)`；与 uploader 未配置分支的既有回收
（`scan_runner.py:283`）语义一致，异常照常向调用方传播。

## Alternatives

- **仅在 `except` 中回收**——放弃：与 `finally` 等价但多一个分支，且未来新增
  异常类型（如 `BaseException` 族）会漏回收；
- **失败时不回收、留待下一轮 prepare**——放弃：正是本缺陷的现状；
- **把回收上移到 `try_begin_host_scan` 的 finally**——放弃：该层级不知道本轮
  staging 路径（`_last_scan_root` 语义是「本轮产物，尚未上传」），会把「成功
  保留给 upload」的窗口一并删掉。

## Verification

- `venv/bin/python -m pytest backend/agent/tests/test_scan_runner.py -q` →
  31 passed（含新增 `test_run_scan_and_upload_reclaims_when_upload_raises`）。
- 反事实验证：把 `scan_runner.py` 还原为 origin/main 版本后，新用例在回收断言处
  失败（staging 残留 `pr42-*`）；恢复修复后通过。

## Revisit

若 upload 将来改为「可重试队列」语义、需要保留 staging 供重试，则应改为
「重试耗尽后回收」而非立即 `finally` 回收——届时同步调整本实现与用例。
