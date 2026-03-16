# ADR-0004: 心跳驱动的主机/设备在线性模型
- 状态：Accepted
- 日期：2026-02-18（2026-03-16 更新）
- 决策者：平台研发组
- 标签：心跳, 在线状态, 设备监控, 数据采样, 会话看门狗

## 背景

平台需要持续判断主机与设备在线状态，并在 UI 实时显示。单次轮询无法覆盖节点抖动与任务执行中的状态变化。

## 决策

以 Agent 心跳作为主机/设备在线性的事实来源：

- Host：`/api/v1/heartbeat` 更新 `last_heartbeat` 和主机状态。
- Device：心跳中携带设备连接状态、硬件/系统指标；服务端按设备维度更新 `last_seen`、`status`。
- 缺失检测：
  - 主机超时由 `hosts` 路由与 recycler 双路径兜底标记 `OFFLINE`。
  - 心跳中未出现且超时的设备标记 `OFFLINE`。
- 采样策略：设备指标快照按间隔降采样，降低数据库写压力。

### 2026-03-16 更新：会话看门狗接管心跳超时检测

Host 心跳超时检测现由 `session_watchdog` 统一管理（`USE_SESSION_WATCHDOG=true` 时）：

- **超时阈值**：`HOST_HEARTBEAT_TIMEOUT_SECONDS`（默认 120s），替代 legacy `heartbeat_monitor` 的 30s 阈值
- **联动行为**：Host 超时 → OFFLINE + RUNNING job → UNKNOWN → 宽限期后 FAILED
- **互斥运行**：`session_watchdog` 与 `heartbeat_monitor` 通过 `USE_SESSION_WATCHDOG` 环境变量互斥切换。启用 watchdog 时 heartbeat_monitor 不启动
- **Recycler 协调**：watchdog 启用时，recycler 跳过 `_check_host_heartbeat_timeout` 和 `_check_device_lock_expiration`

Legacy `heartbeat_monitor`（`backend/tasks/heartbeat_monitor.py`）仍保留用于兼容旧 TaskRun 路径，但默认不启动。

## 备选方案与权衡

- 方案 A：控制面主动轮询主机与设备。
  - 优点：中心可控。
  - 缺点：高成本、跨网段复杂、扩展性差。
- 方案 B：当前方案（Agent 主动上报心跳）。
  - 优点：节点自治，扩展成本低，网络开销可控。
  - 缺点：依赖 HOST_ID 配置正确，错配会出现"心跳正常但无任务"。

## 影响

- 正向影响：主机和设备状态可追踪、可回放，适合监控面板。
- 代价：需要严格管理 Agent 配置一致性，尤其是 `HOST_ID` 对齐。

## 落地与后续动作

- ✅ 心跳接入、设备数据回传、离线判定与通知
- ✅ 会话看门狗接管心跳超时检测，与 heartbeat_monitor 互斥
- ✅ Host 超时联动 Job 状态转换（RUNNING → UNKNOWN → FAILED）
- 后续：引入 Agent 注册握手，降低手工维护 `HOST_ID` 的操作风险

## 关联实现/文档

- `backend/api/routes/heartbeat.py` — 心跳接收端点
- `backend/agent/heartbeat.py` — Agent 心跳发送
- `backend/agent/main.py` — Agent 主循环
- `backend/tasks/session_watchdog.py` — 会话看门狗（Host 超时检测）
- `backend/tasks/heartbeat_monitor.py` — Legacy 心跳超时检测（默认不启动）
- `backend/main.py` — watchdog / heartbeat_monitor 互斥启动
- `backend/scheduler/recycler.py` — 回收器（watchdog 启用时跳过心跳检查）
- `backend/api/routes/hosts.py`
- [`ADR-0003`](./ADR-0003-task-run-state-machine-and-device-lock-lease.md) — 设备锁租约与会话看门狗详细设计
