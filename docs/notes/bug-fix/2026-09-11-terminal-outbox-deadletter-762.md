# 终态 outbox：死信上限 + 队头饿死修复（#762 / #733 follow-up）

Status: implemented
Class: bug-fix

## Decision

背景：PR #733（`e.response is not None`）让 409 分支复活的同时，暴露
`TERMINAL_PAYLOAD_CONFLICT` / unstructured 409 / current 非 ACKABLE 三类
**「命中即永久 retain」**的行没有任何退出路径：只 `bump_terminal_attempt` +
每 15s ERROR 日志，`acked=0` 永不 ack；而 `get_pending_terminals(limit=20)` 按
`created_at` 升序取最旧 → 永久行占满 batch 窗口，其后**新终态行队头饿死**
（#742 给 log_signal/step_trace 上了死信上限，terminal outbox 漏了）。

修复（对齐既有 #9 / #1204 / #742 口径）：

1. `job_terminal_outbox` 增列 `dead_letter`（CREATE 含列 + 幂等
   `_ensure_terminal_outbox_schema()` ALTER，兼容已部署 Agent）；
2. `bump_terminal_attempt` 返回新 attempts（与 `bump_step_trace_attempt` /
   `bump_log_signal_attempt` 同口径），新增 `mark_terminal_dead_letter` /
   `get_terminal_dead_letters` / `count_terminal_dead_letters`；
3. `get_pending_terminals` 过滤 `dead_letter = 0` → 死信行不再占队头；
4. drainer 新增 `_MAX_TERMINAL_ATTEMPTS = 10` 与统一出口
   `_retain_or_dead_letter`：retain 分支（CONFLICT / unstructured /
   current 非 ACKABLE）与**非 409/404 的 4xx（中心永久拒绝）**达上限即死信 +
   ERROR 日志 + `dead_letter_total` 指标；**5xx / 网络错误维持无限重试**
   （瞬时故障不得丢终态事实）；
5. 口径修正：`conflicts_retained_total` / `unknown_retained_total` 是**事件
   计数**（每循环每行 +1，非积压 gauge，docstring 已显式说明）；「卡死行」的
   distinct 口径 = drainer `dead_letter_total` + 心跳新增
   `terminal_outbox_dead_letter_total`（库内死信行数，随 extra 上报）。
6. 死信行**保留不删**：审计可查，且 `has_terminal_fact` 仍认它为「终态事实已
   持久化」的证据（#1005 的恢复删除判据不受影响）。

## Alternatives

- **只给 retain 三类加死信、不覆盖其它 4xx**：非 409/404 的 4xx（如 422
  schema 拒绝）同为永久失败，仍会占队头——一并纳入；5xx/网络错误不纳入；
- **对所有失败（含网络）都用 10 次上限**：3 分钟网络抖动即会让终态事实死信，
  代价高于收益——分永久/瞬时两类处理；
- **死信即删除行**：否决——保留行用于审计与 #1005 事实判据（与 log_signal
  死信保留语义一致）；
- **新增独立 recalim 消费者**：issue 允许「或确认存在 reconcile 消费者」，
  现状没有；加消费者远超本单（死信 + 审计读取已提供退出路径与可见性）。

## Verification

实际运行：

- `pytest backend/agent/tests/test_terminal_outbox_dead_letter.py
  backend/agent/tests/test_terminal_outbox_drainer_metrics.py
  backend/agent/tests/test_terminal_durability.py -q` → **22 passed**
  （新增 11 例：schema 增列/幂等、bump 返回新值、死信不占队头、has_fact 存活、
  prune 保留死信、drainer 达上限死信且新行可 ack；**409 三分支真实 falsy
  Response 回归**——旧测试用 MagicMock（bool 恒 True），正是 409 死代码漏网
  ~4 个月的原因；非 409 的 4xx 走到死信口径）；
- `backend/agent/tests` 全量 → **1592 passed**；
- `ruff check`（3 个改动文件 + 2 个测试文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 真机长跑验证（制造永久 409 行观察 10 次后死信 + 队头恢复）——需构造控制面
  永久冲突，环境受限未执行；单测已覆盖判定逻辑与队列行为。

## Revisit

- 若未来给终态加「replay 死信」入口（log_signal 有 `replay_*`），按同一模式
  在 local_db 增 `replay_terminal_dead_letter`；当前死信保留 + 审计读取已够；
- 上限值 10（= log_signal / step_trace）如现场认为过激/过缓，用常量单点调整；
- 告警接线（把 `terminal_outbox_dead_letter_total` 纳入 Prometheus/看板）属
  R11/可观测域，如需另行开单。
