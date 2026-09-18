# #736 切片：停机序列抽出 `graceful_shutdown`

Status: implemented
Class: bug-fix

## Decision

把 `main()` `finally` 停机体迁到 `shutdown_agent_runtime`；`main` 只保留
`while` / signal / `finally` 接线。#784 顺序契约（archiver / disk /
event uploader 在 watcher `log_signal_drainer` 分支外 stop）随文件搬家，
断言改指 `graceful_shutdown.py`。

同 PR 棘轮：`main.py` 682 → **630**，封顶 **662**（×1.05）。

## Alternatives

- **连同 while 做成 `run_agent_loop`**：弃——claim tick 已独立，停机单独搬家
  更易保住 #784 顺序契约；
- **直接上 `AgentApplication.shutdown`**：弃——先垂直抽出可测函数，再挂薄壳。

## Verification

- graceful_shutdown + lifecycle_784：**14 passed**
- `check:quick`：见 PR

## Revisit

- 下一刀：薄壳 `AgentApplication`（initialize / start_background /
  register_handlers / run_loop / shutdown），或 `_check_agent_version` /
  local_db 启动段。
