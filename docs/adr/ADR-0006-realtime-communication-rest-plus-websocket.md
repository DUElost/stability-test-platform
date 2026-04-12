# ADR-0006: REST + WebSocket 的实时通信分工
- 状态：Accepted（实现层被 ADR-0018 supersede）
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
  - ~~WebSocket 鉴权 token 与前端端点配置尚未完全统一。~~ → 已由 [ADR-0009](./ADR-0009-websocket-auth-and-endpoint-config-unification.md) 解决（2026-03-24）。
  - ~~局部页面仍存在硬编码 WS 地址的问题。~~ → 同上。

## 落地与后续动作

- ✅ 已落地：Dashboard 与 run 日志通道、线程安全广播桥接。
- ✅ WS 鉴权与端点配置已由 [ADR-0009](./ADR-0009-websocket-auth-and-endpoint-config-unification.md) 统一。
- ✅ **WebSocket 实现层已被 ADR-0018 supersede**：自研 `ConnectionManager` 已由 python-socketio 替代，获得 rooms、namespace（`/agent` + `/dashboard`）、Redis adapter 多进程同步、自动断线重连。**REST + 实时推送分工原则保留不变**。详见 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md)。

## 关联实现/文档

### 当前活跃（ADR-0018 迁移后）
- `backend/realtime/socketio_server.py` — python-socketio 服务端（`/agent` + `/dashboard` namespace）
- `backend/realtime/log_writer.py` — 异步日志文件持久化
- `frontend/src/hooks/useSocketIO.ts` — socket.io-client hook（替代 useWebSocket）
- `frontend/src/hooks/useRealtimeDashboard.ts` — Dashboard 实时数据 hook

### Legacy（待 Wave 8 清理）
- `backend/api/routes/websocket.py` — deprecated stub（ConnectionManager no-op）
- `frontend/src/hooks/useWebSocket.ts` — 旧原生 WebSocket hook（生产页面已不引用）
- `frontend/src/config/index.ts` — `WS_*` 常量（仅作 room 解析键）

### 其他关联
- `backend/api/routes/tasks.py` — 兼容层（Wave 7 收敛目标）
- `frontend/src/pages/tasks/TaskDetails.tsx`
- `docs/module-responsibilities.md` — 模块职责定义（含 SocketIO 服务端日志持久化策略）
