# ADR-0004: 心跳驱动的主机/设备在线性模型
- 状态：Accepted
- 日期：2026-02-18
- 决策者：平台研发组
- 标签：心跳, 在线状态, 设备监控, 数据采样

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

## 备选方案与权衡

- 方案 A：控制面主动轮询主机与设备。
  - 优点：中心可控。
  - 缺点：高成本、跨网段复杂、扩展性差。
- 方案 B：当前方案（Agent 主动上报心跳）。
  - 优点：节点自治，扩展成本低，网络开销可控。
  - 缺点：依赖 HOST_ID 配置正确，错配会出现“心跳正常但无任务”。

## 影响

- 正向影响：主机和设备状态可追踪、可回放，适合监控面板。
- 代价：需要严格管理 Agent 配置一致性，尤其是 `HOST_ID` 对齐。

## 落地与后续动作

- 已落地：心跳接入、设备数据回传、离线判定与通知。
- 后续：引入 Agent 注册握手，降低手工维护 `HOST_ID` 的操作风险。

## 关联实现/文档

- `backend/api/routes/heartbeat.py`
- `backend/agent/heartbeat.py`
- `backend/agent/main.py`
- `backend/api/routes/hosts.py`
- `docs/preprod-drill-runbook.md`
- `docs/production-minimum-deployment-checklist.md`
