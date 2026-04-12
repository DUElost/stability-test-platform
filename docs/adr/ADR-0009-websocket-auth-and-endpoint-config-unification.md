# ADR-0009: WebSocket 鉴权与端点配置统一化
- 状态：Superseded（实现层被 ADR-0018 替代，鉴权原则保留）
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
- 接受日期：2026-03-24
- Superseded 日期：2026-04-09（ADR-0018 Phase 3 完成）
- 决策者：平台研发组
- 标签：WebSocket, 配置治理, 安全, 前后端契约

## 背景

当前 WebSocket 存在配置分散与契约不一致问题：

- 前端存在多处 API/WS 基址定义与局部硬编码。
- WS token 传递方式与页面实现不统一。
- 生产代理场景下，端口/路径配置容易漂移。

## 决策

统一 WS 端点与鉴权策略：

- 前端仅保留一个运行时配置入口（含 API_BASE、WS_BASE、WS_TOKEN）。
- 所有页面通过统一 Hook 生成 WS URL，禁止硬编码 `:8000`。
- 统一 token 策略（短期 query token，长期切换为受控鉴权机制）。
- 规范事件信封结构（`type`、`payload`、`timestamp`、可选 `seq`）。

## 备选方案与权衡

- 方案 A：按页面逐步修补，不形成统一规范。
  - 优点：改动小。
  - 缺点：长期不可控，易反复回归。
- 方案 B：集中统一配置与契约（当前提案）。
  - 优点：可维护性高，便于 AI 与团队一致实现。
  - 缺点：需要一次性触达前后端多个模块。

## 影响

- 正向影响：生产部署可预测性提升，安全边界更明确。
- 代价：需要迁移既有页面实现与测试用例。

## 落地与后续动作

> **Superseded by ADR-0018**：本 ADR 的核心成果（统一配置入口、统一 hook、鉴权策略、事件信封）已内化到 ADR-0018 的 python-socketio 迁移中。以下所有工作项均已完成，实现层已切换到 SocketIO。

| 步骤 | 内容 | 状态 | 备注 |
|------|------|------|------|
| 第一步 | 梳理并删除重复配置源 | **已完成** | `utils/config.ts` 已删除；`config/index.ts` 为唯一入口；`.env.example` 已补齐 |
| 第二步 | 统一 `useWebSocket` 入参协议与 token 注入 | **已完成 → 被 ADR-0018 supersede** | 所有页面已迁移到 `useSocketIO.ts`（socket.io-client） |
| 第三步 | WS 契约测试 | **已完成** | `backend/tests/api/test_websocket.py` 保留（deprecated stub 回归测试） |

## 实现现状（2026-04-12 更新）

> 以下为 ADR-0018 迁移后的实际现状。原生 WebSocket 端点仍作为 deprecated stub 保留（见 ADR-0018 Phase 6）。

### SocketIO 端点（当前活跃）

| Namespace | 方向 | 鉴权 | 用途 |
|-----------|------|------|------|
| `/agent` | Agent → Backend | `AGENT_SECRET` 连接认证 | Agent 实时日志/状态推送 |
| `/dashboard` | Backend → Frontend | socket.io-client 自动携带 token | 前端 Dashboard 实时更新 |

### 旧 WebSocket 端点（deprecated stub，待 Wave 8 移除）

| 端点 | 当前行为 | 保留原因 |
|------|---------|---------|
| `/ws/dashboard` | accept + 首包返回 DEPRECATED | 契约测试兼容 |
| `/ws/workflow-runs/{run_id}` | accept + 收包循环 | 部分前端代码残留引用 |
| `/ws/jobs/{job_id}/logs` | accept + 首包返回 DEPRECATED | 同上 |
| `/ws/logs/{run_id}` | accept + 收包循环 | 旧命名兼容 |
| `/ws/agent/{host_id}` | accept + 首包鉴权 | Agent 侧已切 SocketIO |

### 前端配置与 URL 构建

**配置入口**：`frontend/src/config/index.ts` 导出 `API_BASE_URL`、`WS_BASE_URL`、`WS_DASHBOARD_ENDPOINT`。

**当前实际连接方式**：所有生产页面通过 `useSocketIO.ts` 连接 `/dashboard` namespace（socket.io-client），不再使用原生 `WebSocket`。`WS_*` 常量仅作为 `useSocketIO` 内部的 room 解析键使用。

### Agent 客户端

- 已迁移到 `python-socketio` Client 同步版（`backend/agent/ws_client.py`）
- 连接 `/agent` namespace
- 保留指数退避重连和缓冲回放机制

## 保留的设计原则（被 ADR-0018 继承）

以下原则在 SocketIO 迁移后仍然有效：

1. 前端仅通过单一配置入口（`config/index.ts`）获取连接参数
2. 禁止硬编码端口号
3. 事件信封标准化（`type` + `payload` + `timestamp`）
4. 生产环境强制要求有效鉴权

## 关联实现/文档

### 当前活跃
- `backend/realtime/socketio_server.py` — python-socketio 服务端
- `backend/agent/ws_client.py` — Agent SocketIO 客户端
- `frontend/src/hooks/useSocketIO.ts` — socket.io-client hook
- `frontend/src/config/index.ts` — 主配置入口
- `frontend/src/utils/auth.ts` — token 管理

### Legacy（待 Wave 8 清理）
- `backend/api/routes/websocket.py` — deprecated WS stubs
- `backend/tests/api/test_websocket.py` — WS 契约测试（回归保障）
- `frontend/src/hooks/useWebSocket.ts` — 旧原生 WS hook
- `frontend/vite.config.ts` — WS proxy 配置（SocketIO 亦需保留）
