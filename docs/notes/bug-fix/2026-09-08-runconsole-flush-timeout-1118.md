# RunConsole 安静等待时也能按时 flush（#1118）

Status: implemented  
Class: bug-fix

## Decision

为每个 run 增加 daemon flush 定时线程：按 `_FLUSH_MAX_INTERVAL`（默认 100ms）
调用 `flush()`，即使 reader 阻塞在下一行 `readline` 也能推送/落盘已缓冲行。
`buf` 用锁保护；reader 在行数达上限时仍立即 flush。

涉及：`backend/services/run_console.py`；测试见 `test_run_console.py`。

## Alternatives

- `select`/`readline` 超时：Windows 管道不友好，本机部署虽以 Linux 为主，
  定时线程更可移植。
- 仅缩短行缓冲：不解决「只有一行且进程不退出」场景。

## Verification

- `test_flushes_single_line_while_process_stays_quiet`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_run_console.py -q`

## Revisit

若高并发 run 过多定时线程成为负担，可改为共享 scheduler。
