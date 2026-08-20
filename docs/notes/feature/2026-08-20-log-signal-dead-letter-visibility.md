# log_signal 死信控制面可见性与重放

Status: implemented
Class: feature

## Decision

#302（DEVICE_LOG_FLOW_REVIEW_2026-08-09 §二 P4-4）收口：log_signal outbox 死信
不再只留在 Agent 本地 SQLite，控制面可看数、可拉清单、可重放。

- **总数随心跳上报**：`get_outbox_counts` 增加
  `log_signal_dead_letter_total`（`local_db.count_log_signal_dead_letters`，
  死信行保留在 SQLite，跨 Agent 重启累计）；控制面
  `_process_heartbeat_with_db` 把它写入 `host.extra`，`GET /hosts/{id}` 即可
  看到各 host 的历史累计死信数。
- **清单与重放走 SocketIO RPC**：`AgentSocketIOClient._on_control` 改为返回
  control handler 的结果作为 ack payload（现有单向命令不受影响），使
  `call_agent_rpc` 可同步拿到结果；Agent `_handle_control` 新增
  `list_log_signal_dead_letters` / `replay_log_signal_dead_letter` 两个命令。
  重放把死信行重置为 `dead_letter=0, attempts=0, acked=0`，下一个 drain tick
  自然重新入队，无需重启 Agent。
- **控制面 API（admin）**：`GET /api/v1/hosts/{host_id}/log-signal-dead-letters`
  拉清单；`POST .../{row_id}/replay` 重放。Agent 离线 → 503，RPC 超时 → 502，
  行不存在/非死信 → 404。

## Alternatives

- 死信清单通过心跳直接上送摘要：心跳每分钟一次、每次全量上送最近 100 条
  成本高且事件不即时；按需 RPC 拉取更轻，未采用。
- 控制面直接改 Agent SQLite（SSH 远程执行）：侵入 Agent 本地库、绕过锁与
  SocketIO 认证，未采用。
- 重放即删除死信行重新 insert：丢审计历史（attempts/last_error），保留行
  但重置状态更可追溯，未采用删除方案。

## Verification

- `pytest backend/agent/tests/test_outbox_drainer_dead_letter.py`：新增
  count/replay 测试（重置后重新进入 pending、非死信/不存在行返回 False）；
- `pytest backend/tests/api/test_heartbeat_outbox_metric.py`：心跳后
  host.extra 持久化死信总数；
- `pytest backend/tests/api/test_log_signal_dead_letter_api.py`：清单/重放
  端点覆盖 200/403/404/502/503 路径（mock call_agent_rpc）。

## Revisit

若未来死信量级变大需要分页，可在 RPC payload 加 offset/cursor；若控制面要
实时告警而非人工拉取，可在心跳 extra 之外再走 Prometheus gauge
（`record_agent_outbox_pending` 同款）。
