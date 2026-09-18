# #739/#2188 scan 分片登记失败可见化：typed 异常 + worker 专用标记 + 心跳计数器

Status: implemented
Class: bug-fix

## Decision

#2474（#2188 D 步·单2，已合入）在写侧确立契约：`upload_scan_report` 里
`_record_shard_entry` 失败必须 raise，不得吞成 `None`——「文件已落 `dedup/`、
清单未写 `_meta/`」的半交付不得静默。但 2026-09-18 24h 审计发现端到端目标未达成：
`scan_now` 是入队即返回（SocketIO ack ≠ 完成），真正的执行在 worker 线程，而
`_worker_loop` 的 catch-all 把这个 raise 连同其它异常一起降级成一条
`scan_queue_job_failed` 日志——上不抛、不落状态、不计数，注释里宣称的
「raise 后沿 scan_now 传播」路径不存在。

修法（让失败可见，不与普通 scan 失败混淆）：

1. **typed 异常**：`upload_manager.ShardRegistrationError`——`_record_shard_entry`
   的失败统一 wrap 成该类型上抛（原始异常挂 `__cause__`，归因不丢），使半交付
   在类型层面与普通 scan 失败可区分。
2. **worker 区分**：`_worker_loop` 对 `ShardRegistrationError` 走专用分支——
   `scan_shard_register_failed` 标记的 ERROR 日志（含 run/host 与「文件已落、
   清单未写；下轮 scan_now 幂等重写」语义）+ 进程级计数器
   （`_shard_register_failures`，锁保护）。普通异常维持既有
   `scan_queue_job_failed` 口径不动。
3. **心跳上报**：`main.get_outbox_counts` 增
   `scan_shard_register_failure_total`（`ScanRunner.shard_register_failure_total()`），
   与 `terminal_outbox_dead_letter_total` 同通道随心跳上送——agent 无独立 metrics
   面，心跳 extra 的 system_stats 键值自由透传，控面侧无需 schema 改动。

重试语义不变：不 worker 自动重入队（见 Alternatives），自愈依赖下轮 `scan_now`
整段幂等重试（copy 覆盖写、分片幂等重写）；读侧过渡期仍有 legacy glob 兜底。

## Alternatives

- **让异常沿 worker 线程继续上抛**：否决。worker 线程无人接异常，上抛 = 线程死亡，
  正是 #754/#1706 修掉的「单 job 失败带走整个队列」形态。
- **worker 有界自动重入队**：否决（本切片内）。重入队 = 整段重跑两个 subprocess
  （staging 已在 `finally` 回收，org 源文件不可复用）；NFS 短暂抖动下有界重试
  大概率同因失败，持续故障下只是把失败推迟几拍。自然重试杠杆已存在（下轮
  `scan_now` / 控制面重发），且读侧过渡期容忍。若采数显示瞬态抖动占比高，
  再评估只重试「upload+登记」段的轻量重试。
- **落 per-host 死信行 + 控制 RPC 重放**（#302/#1204 模式）：否决（本切片内）。
  分片登记的「重放」= 重跑整轮 scan，没有廉价 replay 语义；`outbox_drainer`
  自身警告过「无 replay 出口的死信行取不回来」。控面按 run/host 标记 scan
  失败并触发重扫属 D 步读侧演进（E 步域），须随 `_meta` 读侧切换一并设计。
- **改注释到事实、承认写侧-only**（审计建议二）：否决单用。与可见化修复并用——
  注释已同步改为真实契约（typed raise → worker 可观测），不是退让式改口径。

## Verification

- `python -m pytest backend/agent/tests/test_scan_runner_shard_failure_visibility.py
  backend/agent/tests/test_scan_runner_worker_guard.py backend/agent/tests/test_upload_manager.py
  backend/agent/tests/test_scan_runner.py backend/agent/tests/test_scan_runner_idle_exit_race_1706.py -q`
  → 51 passed。新增 3 用例锁：typed 失败 → 专用标记 + 计数器 +1 且后续 job 仍被处理；
  普通异常 → 既有标记、计数器不动；计数器跨 job 累计、`_reset_for_tests` 清零。
  `test_shard_write_failure_raises_not_silent`（#2474 遗产用例）改为断言
  `ShardRegistrationError` + `__cause__` 为原始 `OSError` + 半交付文件确实存在。
- `python -m pytest backend/agent/tests/ -q` → **2148 passed**（67.79s，干净环境语义）。
- `main.py` 双布局导入（`agent.` / `backend.agent.` 两分支）均补 `ScanRunner`，
  `py_compile` 通过；心跳键经 `system_stats.update` 自由合并，无控面 schema 依赖。

## Revisit

- 计数器为**进程级累计、重启清零**（与 `log_signal_dead_letter_total` 的跨重启
  库存口径不同）——若告警需要跨重启口径，再评估落 `local_db`。
- 控面消费（按 host 告警 / run×host 粒度失败面板 / 重扫动作）在 #2188 E 步
  （`_meta` 读侧切换）时一并裁决；本切片交付的是 agent 侧信号源。
- `frontend`/告警规则未消费该键；若站点告警要挂它，走 `deploy/prometheus`
  规则变更（那会碰 #2643 的站点可见面分层）。
