# AEE emit 补偿通道：意图 + 幂等重放（#1719，承接 #803 第 1 处）

Status: implemented
Class: bug-fix

## Decision

**本质问题**：AEE 条目的 emit（log_signal outbox）与 DLE 注册，和 processed
落盘是不同原子单元。崩溃窗口只有三种处置：重复（#1687 之前：emit 成功、
状态未落盘 → 重拉重发）、丢失（#1687 折衷版：状态先落盘、emit 未发生 →
永不重发）、或「意图 + 幂等重放」（本单）。#1687 消掉了重复，但把窗口换成
丢失——对崩溃采集是硬伤；本单把丢失窗口堵上。

**机制**（全部围绕「效果发生时，幂等 keys 已在盘上」这一不变式）：

1. `processor._finalize_processed_entry` 在写 processed **之前**调用新增可选
   钩子 `on_entry_intent`（payload 同 on_new_entry + `state_key_prefix`）；
   patrol 路径不传 → 行为不变。reconciler 据此落一条**意图占位**（记录
   payload、entry_origin、首次观测 detected_at）。
2. `reconciler._handle_new_entry` 命中占位 → 分配/复用 keys → **先持久化**
   （`seq_no` + envelope + DLE 的预分配 UUID + 完整 DLE payload）→ 再产生
   效果（outbox enqueue + DLE POST）→ 标 `done`。keys 已存在（崩溃重放）时
   直接复用，不重新分配、不重建 payload。
3. `tick_once` 开头 `_sweep_emit_intents()`：`!done` → 重放（keys 缺失则新
   分配）；`done` 且 line 已 processed → 清理；`done` 且未 processed →
   保留（重拉路径复用 keys，避免重复 emit）。重放失败按
   `MAX_REPLAY_ATTEMPTS=5` 退场并计 `signals_dropped`（防坏数据无限重放）。
4. 支撑改动：
   - `SignalEmitter` 拆 `prepare`/`enqueue`（`emit` 保持为二者组合，向后
     兼容）；`enqueue` 以 **envelope 的 job_id** 为幂等键——AEE processed
     状态按 serial 共享，重放可能发生在后续 Job 的进程里，要落回原 Job 的
     outbox 行；
   - DLE client 拆 `build_local_event_payload` / `build_pull_failed_payload`
     + `post_event_payload`（失败仍入 `dle_register_outbox` 重试；pull_failed
     路径一并补上 event_id 与失败兜底）；
   - 新模块 `backend/agent/aee/emit_intent.py`：意图簿的键命名、读写与记录
     构造（`{processed_key}:emit_intents`，runtime 与
     `watcher_baseline:{job}` 两套命名空间，sweep 均覆盖）。

**重放幂等键**：log_signal = `(job_id, seq_no)`（LocalDB `INSERT OR IGNORE`
+ 后端 `ON CONFLICT DO NOTHING`）；DLE = 预分配 UUID（后端按 id upsert，
#1051/R09-R01 保证同 key 可安全重放）。

**#1823 序号分配闭环（最近 7 天审计 F02）**：只从 outbox 恢复内存计数器
不能覆盖「keys 已持久化、尚未 enqueue」的崩溃窗口。`prepare` 改为在
LocalDB `agent_state` 的 `log_signal_seq:{job_id}` 原子预留高水位，再返回
envelope；仍不创建 outbox 行、不产生网络效果。SQLite 单条 upsert 与事务
保证多个 emitter/连接的预留互斥；允许未使用序号形成空洞，不允许复用。
`enqueue` 在同一事务推进 envelope 原 Job 的高水位，重放不影响当前 Job。

LocalDB 初始化扫描旧 `:emit_intents`，保留尚未进入 outbox 的已分配 keys
（包括仍未清理的 done 记录），避免升级后先到的新信号抢占旧键。新分配取
持久预留与 outbox 最大值，outbox 裁剪或重启不会回退。无需控制面迁移，
不改变 signal / DLE 的既有幂等键与跨 Job 归属。

## Alternatives

- **只在 replay enqueue 后抬升内存计数器**——#1823 否决：重启后新事件可先于
  sweep 到达；必须在 prepare 返回前持久化预留，并恢复旧版本留下的 keys。

- **回退 #1687 的顺序（emit 先、processed 后）**——重复窗口回归，否；
- **把 outbox 写入与 processed 状态收敛进同一 SQLite 事务**——回调链里
  `create_local_event` 是**同步 HTTP POST**，不能跨网络持 SQLite 写锁，
  机制上不可行（本次实现前的关键排除项）；
- **重放时只存 event_id、重建 DLE payload**——放弃：重建依赖重放时刻的
  目录/元数据状态（`parse_metadata` / `dir_size_bytes`），可能与首发不一致；
  存 payload 原样重放最忠实；
- **不做有界重试，坏数据无限重放**——放弃：每轮 tick 刷屏且无审计；5 次
  上限 + `signals_dropped` 计数 + 日志留痕；
- **意图簿只在运行时命名空间做**——放弃：baseline 路径同样经
  `_handle_new_entry`，其窗口一致，必须同覆盖。

## Verification

#1823 本次实际运行：

- `python -m pytest backend/agent/tests/test_emitter.py backend/agent/tests/test_local_db_watcher.py backend/agent/tests/test_aee_emit_intent_1719.py -q`
  → **53 passed**；覆盖关闭/重开 SQLite 后先重放或先发新事件、并发独立连接、
  旧意图迁移、跨 Job 高水位隔离与无 outbox 时的预留保留。
- 变更 Python 文件 Ruff → 通过。
- `python -m pytest backend/agent/tests/ -q` → **1873 passed**；全量首轮发现
  `next_seq_preview` 仍引用旧计数器，修正为持久高水位后重跑通过。
- `python scripts/run_gates.py check:quick` → **7 gates 通过**；测试进程清除
  生产 DB 变量，使用 Agent fixture 的隔离占位配置。

以下为 #1719 历史证据（不代表本次重跑）：

（worktree `/tmp/stp-1719`，基于 `origin/main`）

- `pytest backend/agent/tests/test_aee_emit_intent_1719.py -q` → **16 passed**：
  钩子先于 processed 落盘；占位不覆盖既有 keys；keys 先于效果持久化；done
  幂等重入不重复；sweep 三态（!done 重放 / done+processed 清理 / done 未
  processed 保留）；#1687 窗口补偿（processed 已写、emit 未发生）；重放复用
  seq_no 与 DLE UUID；baseline 命名空间；有界重试退场；tick 接线；emitter
  `prepare/enqueue` 幂等 + envelope job_id 优先；DLE payload 组装与失败入
  outbox 幂等重放；
- **全量回归**：`pytest backend/agent/tests/ -q` → **1809 passed**；
- **反向验证（mutation testing）**：9 个注入缺陷（tick 不 sweep / sweep 不
  重放 / keys 不先持久化 / 重放不复用 seq / done 记录提前清理 / processor
  钩子移到 processed 之后 / done 重入不短路 / enqueue 用当前 job / 占位覆盖
  既有 keys）→ **9/9 被测试捕获**，恢复后 16 passed；每轮清 `__pycache__`；
- `ruff check backend/agent/` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：

- 真机 / 端到端：AEE 崩溃注入 + Agent kill -9 重启，观察 sweep 补偿真实触发
  （本机单测已覆盖崩溃矩阵，真机验证需设备环境）。

## Revisit

- **跨 Job 重放的归属语义**：processed 按 serial 跨 Job 共享，后续 Job 的
  sweep 可能重放前序 Job 的未完成条目（outbox 落回 envelope 的原 job、DLE
  payload 保留原 job/plan_run）。若控制面对已终止 Job 的 log_signal 有额外
  校验（4xx），drainer 会走死信——上线后观察日志再收敛；
- 意图簿的清理依赖 tick 节奏（基线 180s）；极短 Job 的完成记录可能留存到
  同 serial 的下一个 Job 才清（体量为每条目一条 dict，影响可忽略）；
- `post_event_payload` 现在给 PULL_FAILED 路径也加了 outbox 失败兜底（此前
  单发失败即丢）——行为改进，若运维口径要求该路径维持"只发一次"，另单收敛；
- emit 路径若将来改成批量/流式，`prepare`/`enqueue` 的拆分语义需同步审视。
