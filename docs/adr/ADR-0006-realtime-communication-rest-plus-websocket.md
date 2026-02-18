# ADR-0006: REST + WebSocket 的实时通信分工
- 状态：Accepted
- 日期：2026-02-18
- 决策者：平台研发组
- 标签：实时通信, WebSocket, API

## 背景

平台既要支持管理类读写请求，也要支持任务运行中的实时日志与状态更新。单一通信机制难以同时满足易用性与实时性。

## 决策

采用分工明确的通信模式：

- REST：承载 CRUD、任务操作、结果查询、部署触发等请求-响应语义。
- WebSocket：承载运行态增量事件（`RUN_UPDATE`、`DEVICE_UPDATE`、`REPORT_READY`、日志流）。
- 前端策略：WebSocket 实时更新 + React Query 定时轮询兜底，避免连接抖动导致页面数据陈旧。

## 备选方案与权衡

- 方案 A：纯轮询。
  - 优点：实现简单。
  - 缺点：高频接口压力大，实时性差。
- 方案 B：纯 WebSocket。
  - 优点：实时性好。
  - 缺点：重连与状态一致性复杂，首屏数据仍需快照来源。
- 方案 C：当前方案（混合）。
  - 优点：实时与稳定性平衡。
  - 缺点：前后端协议管理复杂度更高。

## 影响

- 正向影响：日志与状态更新响应更快，UI 可观测性增强。
- 风险：
  - WebSocket 鉴权 token 与前端端点配置尚未完全统一。
  - 局部页面仍存在硬编码 WS 地址的问题。

## 落地与后续动作

- 已落地：Dashboard 与 run 日志通道、线程安全广播桥接。
- 后续：见 `ADR-0009`，统一 WS 鉴权与端点配置。

## 关联实现/文档

- `backend/api/routes/websocket.py`
- `backend/api/routes/tasks.py`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/hooks/useRealtimeDashboard.ts`
- `frontend/src/pages/tasks/TaskDetails.tsx`
