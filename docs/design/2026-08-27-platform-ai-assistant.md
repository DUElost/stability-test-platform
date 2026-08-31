# 平台 AI 助手设计（ADR-0031 配套设计文档）

- 日期：2026-08-27（2026-08-29 随二轮审核采纳补齐）
- 性质：**设计文档（how，与代码对齐）**。自治边界与架构决策的裁决见 [ADR-0031](../adr/ADR-0031-platform-ai-assistant.md)，实施过程记录见 [Agent Note](../notes/feature/2026-08-28-platform-ai-assistant.md)。

## 1. 组件与职责

```
前端 /assistant（AssistantPage + SessionList/MessageBubble/ActionCard/LogPanel）
  │  HTTP（轮询 2s，仅 pending 时）
  ▼
/api/v1/ai-assistant（routes/ai_assistant.py，13 端点）
  │
  ├─ 配置面（admin）：GET/PUT /config（Fernet 掩码）· POST /config/test-connection
  ├─ 会话面（登录用户）：/sessions CRUD + /messages（严格按用户隔离）
  └─ 动作面：/actions 详情/日志（提案人或 admin）· approve/reject/cancel（admin）
  │
  ▼
services/ai_assistant/orchestrator.py —— 轮次编排（SAQ 任务 ai_assistant_turn_task）
  ├─ llm_client.py：手写 httpx OpenAI 兼容 /chat/completions（载体可逆，切换面=单模块）
  └─ tools.py：工具注册表（T0 观测×14 / T1 门禁×3 / T2 运维×3；T3 零注册）
```

## 2. 轮次时序

1. `POST /sessions/{id}/messages`：落 user 消息 + **pending 占位**（不入 LLM 历史）→ 入队 SAQ（`retries=0`，超时 = `request_timeout_seconds × max_turns + 120`）。
2. 轮次任务：载历史（截断 20 条，跳过 pending/失败占位）→ system prompt → 循环 ≤ `max_turns`：
   - **T0**：`execute_query` 直读（真库 group_by 全分布，零枚举猜测）；结果以 tool 消息回填继续循环。
   - **T1 自动 / T2 白名单**：建 action（status=approved，decided=发起人）→ RunConsole/服务执行 → **止轮**。
   - **T2 普通**：建 action（proposed）→ **止轮**等审批。
   - 无 tool_calls：落最终回复，结束。
3. 执行完成（RunConsole on_complete / 服务返回）：`_finalize_action` 回写状态 + 落「[执行回执]」消息 → 入队**续轮**（结果以 user 角色注入，规避 tool 无前置 tool_calls 的严格校验）。
4. **收口保证（H1）**：轮次 finally 收口 pending 占位——已产出真实回复则删除占位；未产出（异常路径）则标 failed 留错误可见。任何退出路径不留 pending。

## 3. 状态机

**ai_assistant_action**：
`proposed → approved → running → succeeded|failed|cancelled`；`proposed → rejected`。
- 审批/取消 = admin；`execute_action` 闸门只认 `approved`（proposed 不可直启）。
- RunConsole run 装看门狗 Timer（`timeout_seconds` 到点仍 RUNNING 才取消，走正常 on_complete 回填）。

**ai_chat_message（assistant）**：`pending → completed | failed`；轮次 finally 保证收敛。

## 4. 权限与隔离矩阵

| 资源 | 普通用户 | admin |
|------|----------|-------|
| 会话列表/消息/删除 | 仅本人（严格隔离，404 语义） | 同左（无跨用户通道） |
| 动作详情/日志 | 本人提案的 | 任意（审批职责所需） |
| 动作审批/取消 | ✗ 403 | ✓ |
| 工具面 | `allowed_tool_names(False)`：T0（除 admin 镜像）+ T1 + 非 admin-only 的 T2 | 全部 20 个 |
| 配置 | ✗ 403 | ✓（变更入审计） |

工具面双门禁：payload 按角色过滤 + 执行面按 `allowed_tool_names` 与 `user_may_invoke_tool`（发起人）校验。`admin_only` 工具对普通用户等同「不存在」；`auto_approve` 仅当发起人本身有权调用时才可自动执行（D8）。

## 5. 安全边界

- argv 全服务端模板（LLM 只填参数；枚举/区间/路径穿越/标识符卫生逐参手写校验，校验失败报错占轮次）。
- `run_agent_tests` / `run_quality_gate` 显式注入 `AGENT_TEST_ENV` 四键（防生产 `DATABASE_URL` 经环境继承透传）。
- `reload_config` 走 `call_agent_control_sync`（ack 桥接到主事件循环，未 ack 即失败）。
- API Key：Fernet 独立密钥域（`AI_ASSISTANT_FERNET_KEY`），GET 掩码、PUT 留空不变更；密钥与明文永不进 prompt/日志/审计/消息（有结构断言）。
- 审计：config 变更、会话删除、动作提案/批准/拒绝/取消全量 `record_audit`。

## 6. 部署与观测

1. `.env.backend`：`AI_ASSISTANT_FERNET_KEY`（Fernet 生成）。
2. `stability-backend-migrate.service` → 重启 `stability-backend.service`。
3. 前端随常规 dist-prod 替换。
4. 观测：`/health` 的 `saq_ready`/`admission_queue_*`/`*_enabled`（注意后者是 opt-in 配置开关，非连接状态）；LLM 失败落 `message.meta.error`；编排异常看 `ai_turn_*` 日志。

## 7. 已知边界（重议条件见 ADR）

- 单轮纯问答无流式输出（轮询）；`backend/tests/` 需 PG 不进 T1；hot-update 类 T3 不提供工具入口。
- 每会话轮次串行（job key `ai-turn:{id}`）；跨会话并发受 SAQ 并发与 LLM 供应商限流约束。
