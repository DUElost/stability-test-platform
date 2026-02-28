# 消息队列规范：Redis Stream

## 1. Topic 设计

| Topic 名 | 优先级 | 生产者 | 消费者 | 内容 |
|---|---|---|---|---|
| `stp:status` | **高** | Agent | Server | Job / Step 状态变更事件 |
| `stp:logs` | 低 | Agent | Server / WebSocket | 设备日志流（INFO / WARN / ERROR）|
| `stp:control` | 高 | Server | Agent | 背压指令、工具更新通知 |

**核心规则**：
- `stp:status` 和 `stp:logs` **必须分离**，禁止将日志混入状态 Topic
- 背压机制只能限制 `stp:logs` 的写入速率，**绝对不能** 限制 `stp:status`

## 2. 消息格式

### stp:status — 状态事件

```json
{
  "msg_type":     "job_status" | "step_trace",
  "host_id":      "host-bj-01",
  "job_id":       1234,
  "timestamp":    "2026-02-27T10:00:00.000Z",   // Agent 本地原始时间戳

  // msg_type = "job_status" 时：
  "status":       "RUNNING",
  "reason":       "",

  // msg_type = "step_trace" 时：
  "step_id":      "run_monkey",
  "stage":        "execute",
  "event_type":   "STARTED" | "COMPLETED" | "FAILED",
  "output":       "...",
  "error_message": null
}
```

### stp:logs — 日志流

```json
{
  "job_id":    1234,
  "device_id": 567,
  "level":     "INFO" | "WARN" | "ERROR",
  "tag":       "MonkeyRunner",
  "message":   "...",
  "timestamp": "2026-02-27T10:00:01.123Z"
}
```

### stp:control — 控制指令

```json
{
  "target_host_id": "host-bj-01",   // 或 "*" 表示广播
  "command":        "backpressure" | "tool_update",

  // command = "backpressure" 时：
  "log_rate_limit": 5,               // null 表示解除限速

  // command = "tool_update" 时：
  "tool_id":    42,
  "version":    "v2.2",
  "download_url": "http://server/tools/42/v2.2"
}
```

## 3. ACK 机制

```
Agent 写入 stp:status
  └── Server consumer 读取
       └── 持久化到 DB (step_trace 表)
            └── 执行 XACK stream_key group_name message_id
                 └── 若超时未 ACK → 消息在 PEL (Pending Entry List) 中保留
                      └── Server 重启后重新消费 PEL
```

**Agent 侧**：
- Agent 不直接依赖 Redis ACK，使用本地 `last_ack_id` 跟踪
- `last_ack_id` 含义：最后一条已被 Server 持久化确认的消息 ID
- `last_ack_id` 更新时机：Server 在 heartbeat 响应中返回已处理的最大 message_id

## 4. 断连重放（Replay）

```python
# Agent 重连时执行
def replay_missed_traces(server_client, local_sqlite):
    # 1. 从 Server heartbeat 响应获取已确认的 last_ack_id
    server_last_ack = server_client.get_last_ack(host_id=self.host_id)

    # 2. 从本地 SQLite 读取 last_ack_id 之后的所有 StepTrace
    missed = local_sqlite.query(
        "SELECT * FROM step_trace_cache WHERE id > ? ORDER BY original_ts ASC",
        (server_last_ack,)
    )

    # 3. 批量重放，Server 端幂等处理
    if missed:
        server_client.batch_submit_step_traces(missed)
```

## 5. 背压实现

```python
# Server 消费者监控积压量
async def monitor_backpressure():
    info = await redis.xinfo_groups("stp:status")
    lag = info[0]["lag"]

    if lag > BACKPRESSURE_THRESHOLD:  # 默认 5000 条
        # 通知所有活跃 Agent 降低日志上报频率
        await redis.xadd("stp:control", {
            "target_host_id": "*",
            "command": "backpressure",
            "log_rate_limit": 5      # 每秒最多 5 条 INFO 日志
        })
    elif lag < BACKPRESSURE_RELEASE_THRESHOLD:  # 默认 500 条
        await redis.xadd("stp:control", {
            "target_host_id": "*",
            "command": "backpressure",
            "log_rate_limit": None   # 解除限速
        })
```

## 6. Redis Stream 配置

```python
# 消费者组配置
STREAM_CONFIG = {
    "stp:status": {
        "group":      "server-consumer",
        "maxlen":     100_000,      # 最多保留 10 万条消息
        "block_ms":   1000,
    },
    "stp:logs": {
        "group":      "log-consumer",
        "maxlen":     500_000,
        "block_ms":   500,
    },
    "stp:control": {
        "group":      "agent-consumer",
        "maxlen":     10_000,
        "block_ms":   2000,
    },
}

# 初始化（Server 启动时执行）
for stream, cfg in STREAM_CONFIG.items():
    try:
        await redis.xgroup_create(stream, cfg["group"], id="0", mkstream=True)
    except ResponseError:
        pass  # 消费者组已存在，忽略
```
