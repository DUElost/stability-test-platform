# 本地状态一致性：prune 守卫按 MAX(seq_no)（#803 第 2 处）

Status: implemented
Class: bug-fix

## Decision

**第 2 处（本单修复）**：`prune_acked_log_signals` 的 docstring 声称「每 job
保留 seq_no 最大的一行」，SQL 实为 `MAX(id) GROUP BY job_id`。多写入方
（reconciler 线程 / batcher flusher / puller worker）并发时 seq 分配序与
SQLite 行 id 序可以相反——长 Job 超 keep_recent 后可能删掉 seq 最大行：
重启 `next_log_signal_seq_no`（`MAX(seq_no)`，与本守卫应为同一口径）回退、
复用已上送 seq，后端 `ON CONFLICT DO NOTHING` 静默吞新信号而 Agent 照常
ack。

修复：守卫子查询改 `(job_id, seq_no) IN (SELECT job_id, MAX(seq_no) ...
GROUP BY job_id)`——注释与 SQL 对齐，保留口径统一为 MAX(seq_no)。

## Alternatives

- **恢复端改按 MAX(id) 口径**——放弃：seq_no 是对后端的幂等键（重启恢复
  必须接在已上送最大 seq 之后），id 只是本地行序；恢复端口径不能动，
  修正守卫。

## Verification

- **反例实证**：回退 local_db 实现保留测试 → 用例失败（prune 后
  `next_log_signal_seq_no`=2，应为 6）；修复版全绿；
- 新增用例（`test_local_db_watcher.py` +1）：先插 seq=5 后插 seq=1（制造
  id 与 seq 相反序）→ prune(keep_recent=1) 后下一 seq 仍为 6；
- `test_local_db_watcher.py` 全套 **15 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- **同单第 1 处（processor emit/落盘崩溃窗口）未修、建议拆独立单**：
  `_finalize_processed_entry` 顺序 = 回调（emit log_signal + 注册 DLE）→
  写 processed/pending。两步不同原子单元（回调链各自写 local_db 事务、
  processed 另写），崩溃窗口产生「同一次崩溃两条 log_signal（不同
  seq_no）」。简单互换顺序会把「重复」换成「丢失」（回调失败现状被吞，
  swap 后崩溃前窗口行已标 processed 永不 emit），两者都不正确；正确解
  需二者之一：(a) 回调的 outbox 写入与 processed 状态写入收敛到同一
  SQLite 事务（跨方法事务传递，改动面在回调链）；(b) 引入 emit 补偿
  通道（记录 last_emit_attempt + 重放循环）。两者均为机制设计，属独立
  单范围；
- 本 PR 以 `Refs #803` 关联、**不关闭 issue**——第 1 处跟踪于 issue 内。
