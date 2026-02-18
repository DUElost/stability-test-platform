# ADR-0009: WebSocket 鉴权与端点配置统一化
- 状态：Proposed
- 优先级：P0
- 目标里程碑：M1
- 日期：2026-02-18
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

- 第一步：梳理并删除重复配置源。
- 第二步：统一 `useWebSocket` 入参协议与 token 注入。
- 第三步：为 `/ws/dashboard`、`/ws/logs/*` 增加契约测试。

## 关联实现/文档

- `backend/api/routes/websocket.py`
- `frontend/src/hooks/useWebSocket.ts`
- `frontend/src/config/index.ts`
- `frontend/src/utils/config.ts`
- `frontend/src/pages/tasks/TaskDetails.tsx`
- `docs/production-minimum-deployment-checklist.md`
