# run_console flush 串行化：seq 序与落盘序一致（#1275）

Status: implemented
Class: bug-fix

## Decision

`backend/services/run_console.py` 的 `flush()` 由两个线程并发调用：reader 线程
（`len(buf) >= _FLUSH_MAX_LINES`=50）与 #1118 新增的 `timed_flush` 定时线程
（每 `_FLUSH_MAX_INTERVAL`=0.1s）。原实现只在「摘批」（`buf_lock`）与「分配
seq」（`run._lock`）两步加锁，**文件 append 与 `_do_emit` 处于无锁区**：两个
flush 可分别摘到 seq 1..N 与 N+1..M 后倒序落盘，使文件行序 ≠ seq 序——`read_log`
的行号回溯与实时 `console_log.from_seq` 据此错位。

修复：`ConsoleRun` 增加 `_flush_lock`，`flush()` 全程持该锁，把
「摘批 → 分配 seq → 落盘 → 推送」变成不可交错的单写者临界区。锁序固定为
`_flush_lock → buf_lock → run._lock`，仅 flush 路径获取 `_flush_lock`，无环。

## Alternatives

- **把落盘/推送并入 `run._lock` 临界区**——放弃：`run._lock` 同时保护 status/
  cancel/终态快照，在其中执行文件 IO 与 SocketIO 推送会拉长状态读的阻塞面；
  且既有约定是 `_do_emit` 在锁外调用（见 `_finalize`）。
- **去掉定时线程、回到单线程 flush**——放弃：#1118 的静默期定时落盘是有意
  设计（reader 阻塞在 readline 时仍要有输出），撤回会复现其修复的缺陷。
- **单写者队列（queue.Queue + 专职 writer 线程）**——放弃：改动面与线程数
  更大，当前仅需保证互斥即可满足顺序不变量。

## Verification

- `venv/bin/python -m pytest backend/tests/services/test_run_console.py -q` →
  **16 passed**（含新增 `test_flush_is_serialized_across_threads`，以 emit 回调
  阻塞首个 flush 并观测并发进入 emit 的次数）。
- 反事实验证：把 `run_console.py` 还原为 origin/main 版本后，新用例稳定
  `assert 2 == 1` 失败（两个 flush 并发进入 emit）；恢复修复后通过。
- `python scripts/run_gates.py check:quick` → **[OK]（7 gates）**。

## Revisit

若将来为「多 run 共享 writer 线程」做统一调度（吞吐优化），本锁可由队列替代，
但必须先证明新调度仍保证同一 run 内 seq 序与落盘序同序。
