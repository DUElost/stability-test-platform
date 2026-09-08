# DLE 注册失败持久补偿 + 创建幂等（#1042 / #1051）

Status: implemented
Class: bug-fix

## Decision

1. **Agent**：`create_local_event` 预分配 UUID 写入 POST；HTTP/网络失败时把
   完整 payload 落入 LocalDB `dle_register_outbox`（按 `event_id` 幂等）。
   `EventUploader` 30s 恢复轮询顺带 `drain_register_outbox` 重放；控制面按
   同一 id 插入或更新，补建缺失 DLE。`main` 在 LocalDB 初始化后
   `bind_local_db`。

2. **控制面（R09-R01 / #1051）**：ingest 支持「带 id 且行不存在 → 插入」；
   无 id 时若 `(job_id, signal_seq_no)` 已存在则返回该行，避免重放双插。

涉及：`device_log_event_client.py`、`local_db.py`、`event_uploader.py`、
`main.py`、`agent_api.py`；测试见
`test_dle_register_outbox_1042.py`、`test_agent_device_log_events.py`。

## Alternatives

- 仅依赖 log_signal outbox：信号可补，但不会重建 DeviceLogEvent；放弃。
- 失败时不推进 processed：拉盘与注册耦合，阻塞后续 db_history；改为意图
  持久化后允许推进。

## Verification

- `pytest backend/agent/tests/test_dle_register_outbox_1042.py`
- `pytest backend/tests/api/test_agent_device_log_events.py -k idempotent`
- 关注：失败入队 → drain 成功 ACK；同 client id / 同 job+seq 不双插

## Revisit

若需 DB 级唯一约束 `(job_id, signal_seq_no)`，另开 Alembic；当前应用层
查重足够覆盖 Agent 重试。无 LocalDB 绑定时失败仍只打 fallback 日志（与改前
同形）。
