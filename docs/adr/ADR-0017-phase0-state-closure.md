# ADR-0017: Phase 0 状态闭环
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-04-07
- 接受日期：2026-04-07
- 决策者：平台研发组
- 标签：状态闭环, Agent, 心跳, 补偿路径, 幂等

## 背景

平台在 Agent 终态上报、主机与设备心跳、以及终态补偿处理上长期存在多路径写状态的问题，主要表现为：

- Agent 调用终态上报失败后，Job 可能长期停留在 `RUNNING`
- HTTP heartbeat 与 WS heartbeat 同时参与状态写入，导致 host/device 在线状态存在双写漂移
- `agent_api.complete_job`、MQ consumer、watchdog、recycler 都可能推进终态并触发聚合、锁释放或 post-completion，状态归属不清晰
- `PENDING_TOOL` 等中间状态一旦缺乏收敛路径，workflow 可能长期无法进入终态

这些问题会直接导致任务卡死、监控失真，以及补偿路径与主路径互相踩踏。

## 决策

### 1. 终态主路径

Agent 终态上报以 `POST /api/v1/agent/jobs/{job_id}/complete` 为唯一主入口。

Agent 侧采用本地 SQLite outbox 机制保证终态事件持久化：

- 终态事件先写本地 outbox，再发 HTTP
- HTTP 成功后 ACK
- 未 ACK 事件由后台 drain 线程持续重试
- Agent 关闭时执行同步 drain，尽量刷出剩余终态事件

### 2. Outbox 冲突语义

`complete_job` 在终态冲突时返回结构化 `409 Conflict`，包含：

- `message`
- `current_status`
- `requested_status`

Agent 的 outbox drain 按以下规则处理：

- 若 `current_status` 已属于终态集合，则视为服务端已收敛，ACK 本地 outbox
- 若 `current_status` 不是终态，则视为真实冲突，保留条目并继续重试
- 若返回 `404 Not Found`，则停止重试并 ACK，避免无意义挂起

禁止将所有 `409` 一律视为 ACK。

### 3. Heartbeat 单写原则

`POST /api/v1/heartbeat` 是 host 与 device 状态的唯一持久化路径。

其职责包括：

- 更新 `host.last_heartbeat`
- 更新 `host.status`
- 更新 device 的 `last_seen`、连接状态、电量、温度等完整快照

WebSocket heartbeat 仅用于 dashboard 实时推送：

- 可以承载实时设备信息广播
- 不得写入数据库
- 丢失不会影响权威状态

### 4. 补偿路径职责边界

以下路径被定义为补偿路径，而非主路径。

#### MQ consumer

用于处理终态消息乱序或 HTTP 主路径未及时生效的场景。

规则：

- 若 Job 已终态，则跳过 DB 写入，仅做必要广播
- 仅当 consumer 自己实际完成了状态推进时，才触发 post-completion

#### session_watchdog

用于处理 host 失联、锁过期、`UNKNOWN` 宽限收敛、`PENDING_TOOL` 超时等场景。

规则：

- 负责状态收敛与 workflow 聚合
- 不负责主路径 post-completion

#### recycler

用于处理 `PENDING` 调度超时和 `RUNNING` 运行超时。

规则：

- 只做状态推进、聚合、锁释放和 WS 广播
- 不直接触发 post-completion 或通知
- 对于 `post_processed_at IS NULL` 的终态 Job，在宽限期后执行延迟补偿

### 5. Post-completion 幂等

`job_instance.post_processed_at` 是 post-completion 的唯一幂等标记。

约束如下：

- 主路径和补偿路径都必须先检查 `post_processed_at`
- 已写入 `post_processed_at` 的 Job 不得重复生成报告或通知
- 延迟补偿仅处理终态且 `post_processed_at IS NULL` 的 Job

## 影响

### 正向影响

- 终态不会因瞬时网络失败而永久丢失
- host/device 状态只有一条权威写路径
- 补偿路径不再直接主导完整后处理流程，竞态显著减少
- workflow 终态聚合与报告生成链路更可解释

### 代价与约束

- Agent 侧引入本地 outbox 与后台重试线程
- post-completion 不再保证立即执行，而是主路径优先、补偿延迟兜底
- 需要持续监控 outbox backlog、`409` 冲突率、deferred post-completion 数量与 heartbeat 成功率

## 非目标

本 ADR 不解决以下问题：

- 千级设备规模化调度
- dashboard 分页、虚拟化与批量广播
- Monkey 专项测试能力
- 长稳告警产品化与报表导出

## 护栏

后续演进必须遵守以下约束：

- 禁止新增第二条 DB 写 heartbeat 路径
- 禁止补偿路径绕过状态机直接 force-set 终态
- 禁止在未检查 `post_processed_at` 的情况下重复触发 post-completion
- 禁止把所有 `409` 一律视为 ACK
- 禁止让补偿路径重新变成常规主写路径

## 验证要求

Phase 0 收尾验证至少应覆盖：

- `409 + current_status=RUNNING` 不 ACK、继续重试
- `409 + current_status=FAILED` ACK
- SIGTERM 期间 `drain_sync()` 能刷出 pending outbox
- orphan terminal job 在宽限期后补齐 `post_processed_at`

## 关联实现

- `backend/agent/main.py`
- `backend/api/routes/agent_api.py`
- `backend/api/routes/websocket.py`
- `backend/mq/consumer.py`
- `backend/tasks/session_watchdog.py`
- `backend/scheduler/recycler.py`
- `backend/services/post_completion.py`
