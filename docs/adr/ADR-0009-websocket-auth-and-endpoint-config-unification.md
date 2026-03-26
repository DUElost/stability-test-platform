# ADR-0009: WebSocket 鉴权与端点配置统一化
- 状态：Accepted
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
- 接受日期：2026-03-24
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

| 步骤 | 内容 | 状态 | 备注 |
|------|------|------|------|
| 第一步 | 梳理并删除重复配置源 | **已完成** | `utils/config.ts` 已删除；`config/index.ts` 为唯一入口；`.env.example` 已补齐 |
| 第二步 | 统一 `useWebSocket` 入参协议与 token 注入 | **已完成** | 所有页面（Dashboard、LogsPage、TaskDetails、WorkflowRunMatrixPage）均通过 hook |
| 第三步 | 为 `/ws/dashboard`、`/ws/logs/*` 增加契约测试 | **已完成** | `backend/tests/api/test_websocket.py`：连接/鉴权 + 信封格式验证 |

## 实现现状（2026-03-24 审计）

### 后端 WebSocket 端点

| 端点 | 用途 | 鉴权方式 |
|------|------|----------|
| `/ws/dashboard` | Dashboard 实时推送 | query `token`（`WS_TOKEN` 或 JWT） |
| `/dashboard` | 旧别名 → 同上 | 同上 |
| `/ws/workflow-runs/{run_id}` | Workflow 运行状态 | 同上 |
| `/ws/jobs/{job_id}/logs` | Job 日志（含 Redis 回放） | 同上 |
| `/ws/logs/{run_id}` | TaskRun 日志（旧命名） | 同上 |
| `/ws/agent/{host_id}` | Agent 上行链路 | 首条消息 `{"type":"auth","agent_secret":"..."}` |

**浏览器侧鉴权**（`_validate_ws_token`）：接受 `WS_TOKEN` 静态密钥或 JWT；生产环境（`ENV=production`）强制要求有效 token。开发环境降级默认 `dev-token-12345`。

**Agent 侧鉴权**：首条 JSON 消息携带 `agent_secret`，与 `AGENT_SECRET` 环境变量比对。

### 前端配置与 URL 构建

**配置入口**：`frontend/src/config/index.ts` 导出 `API_BASE_URL`、`WS_BASE_URL`、`WS_DASHBOARD_ENDPOINT`。默认值含 `localhost:8000`（开发环境），生产通过 `VITE_*` 环境变量覆盖。Vite proxy（`vite.config.ts`）将 `/ws` 转发至后端。

**各页面合规状态**：

| 页面 | URL 构建 | Token 注入 | 合规 |
|------|----------|-----------|------|
| `Dashboard.tsx` | `WS_DASHBOARD_ENDPOINT`（from config） | 通过 hook | 合规 |
| `LogsPage.tsx` | `window.location.host` + hook | `upsertWsToken` | 合规 |
| `TaskDetails.tsx` | `window.location.host` 手动拼接 | `localStorage.getItem` + 硬编码 `dev-token-12345` 降级 | **不合规** |
| `WorkflowRunMatrixPage.tsx` | `window.location.host` + 原生 `new WebSocket()` | `ensureFreshAccessToken` + `upsertWsToken` | **部分合规**（未走 hook） |

### 事件信封格式

| 消息类型 | 当前格式 | 是否符合 `{type, payload, timestamp}` |
|----------|----------|--------------------------------------|
| `STEP_LOG` / `STEP_UPDATE` | `{type, payload: {...}}` | 部分（缺 `timestamp`） |
| `DEVICE_UPDATE` | `{type, payload: device_data}` | 部分（缺 `timestamp`） |
| `JOB_STATUS` / `WORKFLOW_STATUS` | `{type, payload: {...}, timestamp}` | **已修复（2026-03-24）** |

### Agent 客户端（`ws_client.py`）

- URL 从 `api_url` HTTP→WS 自动转换（非独立 `WS_URL` 环境变量）
- 重连：指数退避 1s → 可配置上限（`WS_RECONNECT_MAX_DELAY` 环境变量，默认 30s）
- 缓冲：可配置容量（`WS_BUFFER_SIZE` 环境变量，默认 1000 条），重连后回放
- 保活间隔可配置（`WS_PING_INTERVAL` 环境变量，默认 30s）

## 已知问题（已全部修复）

> 以下问题在 2026-03-24 实施中已修复，保留记录供审计。

### ~~1. 死代码配置文件~~ — 已修复

`frontend/src/utils/config.ts` 已删除。

### ~~2. TaskDetails.tsx 硬编码 token~~ — 已修复

移除手动 `localStorage` token 拼接，改用 `useWebSocket` hook 的 `authMode: 'auto'` 自动注入。

### ~~3. WorkflowRunMatrixPage.tsx 绕过 hook~~ — 已修复

主组件和 `JobLogStream` 子组件均已迁移到 `useWebSocket` hook，移除原生 `new WebSocket()` 和手动 auth/reconnect 逻辑。

### ~~4. 事件信封不一致~~ — 已修复

- Workflow 事件 `job_status` → `JOB_STATUS`、`workflow_status` → `WORKFLOW_STATUS`，均包裹为标准 `{type, payload, timestamp}` 信封。
- 所有后端广播消息（`DEVICE_UPDATE`、`LOG`、`STEP_LOG`、`STEP_UPDATE`、`RUN_UPDATE`、`TASK_UPDATE`、`REPORT_READY`）均已补充 `timestamp` 字段。

### ~~5. DeviceMonitorPanel 前后端消息格式不匹配~~ — 已修复

前端已对齐后端格式：`DEVICE_UPDATE` + `payload.id`。

### ~~6. 环境变量文档缺失~~ — 已修复

`backend/.env.example` 已补齐 `ENV`、`WS_TOKEN`、`AGENT_SECRET`。`ws_client.py` 已接入 `WS_RECONNECT_MAX_DELAY`、`WS_PING_INTERVAL`、`WS_BUFFER_SIZE` 环境变量。

## 工作项清单

| # | 任务 | 状态 | 完成日期 |
|---|------|------|----------|
| 1 | 删除 `frontend/src/utils/config.ts` 死代码 | **已完成** | 2026-03-24 |
| 2 | `TaskDetails.tsx` 改用 `useWebSocket` hook + 统一 token 注入 | **已完成** | 2026-03-24 |
| 3 | `WorkflowRunMatrixPage.tsx` 改用 `useWebSocket` hook | **已完成** | 2026-03-24 |
| 4 | 统一事件信封：workflow 事件包裹为 `{type, payload, timestamp}` | **已完成** | 2026-03-24 |
| 5 | 所有后端广播消息补充 `timestamp` 字段 | **已完成** | 2026-03-24 |
| 6 | 修复 `DeviceMonitorPanel` 消息格式匹配 | **已完成** | 2026-03-24 |
| 7 | `backend/.env.example` 补齐 `WS_TOKEN`、`AGENT_SECRET`、`ENV` | **已完成** | 2026-03-24 |
| 8 | `ws_client.py` 接入 `.env` 配置变量替代硬编码常量 | **已完成** | 2026-03-24 |
| 9 | WS 契约测试（`/ws/dashboard`、`/ws/logs/*`、`/ws/jobs/*/logs`） | **已完成** | 2026-03-24 |

## 关联实现/文档

- `backend/api/routes/websocket.py` — 所有 WS 端点 + 鉴权逻辑 + 标准信封广播
- `backend/agent/ws_client.py` — Agent WS 客户端（支持 env 配置）
- `backend/core/security.py` — JWT `decode_token`
- `backend/tests/api/test_websocket.py` — WS 契约测试
- `frontend/src/config/index.ts` — 主配置入口
- `frontend/src/hooks/useWebSocket.ts` — 统一 WS hook
- `frontend/src/utils/auth.ts` — `upsertWsToken`、`ensureFreshAccessToken`
- `frontend/src/pages/tasks/TaskDetails.tsx` — 已对齐 hook token 注入
- `frontend/src/pages/execution/WorkflowRunMatrixPage.tsx` — 已迁移至 useWebSocket
- `frontend/src/components/device/DeviceMonitorPanel.tsx` — 已对齐消息格式
- `frontend/vite.config.ts` — WS proxy 配置
- `backend/.env.example` / `backend/agent/.env.example` — 环境变量文档
- `docs/production-minimum-deployment-checklist.md`
