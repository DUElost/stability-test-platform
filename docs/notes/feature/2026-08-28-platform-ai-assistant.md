# 平台 AI 助手实现（ADR-0031 阶段二：后端 + 前端接线）

- 日期：2026-08-28
- 类型：feature

## 决定了什么

按 [ADR-0031](../../adr/ADR-0031-platform-ai-assistant.md)（v1.3）与[实施计划](../../reviews/AI_ASSISTANT_PLAN_2026-08-27.md)（v1.3）落地 AI 助手全栈：

1. **后端零新增依赖**：`models/ai_assistant.py` 四表（config 单行/会话/消息/动作）+ 迁移 `q9r8s7t6u5v4`（down_revision=`i5j6k7l8m9n0`，alembic 实测单 head）；`core/ai_security.py` Fernet（独立 `AI_ASSISTANT_FERNET_KEY`，TESTING 兜底）；`services/ai_assistant/`（llm_client 手写 httpx OpenAI 兼容 + tools 注册表 14 工具 + orchestrator SAQ 轮次）；路由 `/api/v1/ai-assistant` 13 端点（权限矩阵与审计对齐 D6）。
2. **可行性分析风险 #8 采纳为设计**（计划 v1.3）：T1 自动路径与 T2 审批路径**同构**——都建 action、都经 RunConsole/服务执行、都由 on_complete 入队**续轮**；轮次任务只含 LLM 调用 + T0 快查，天然短（`enqueue_sync` 默认 60s timeout 的约束被结构性绕开）。
3. **T2 白名单后端强制**：PUT config 时 drop 非 `whitelistable` 的 T2 名单项（前端勾选只是 UI，权威在后端）。
4. **前端**：助手页 + 设置页 + pinned 入口 + mock 脚手架（`STP_AI_UI_MOCK=1`，后端就绪后删除）。

## 放弃的备选

- **T1 阻塞等待结果再回复**（计划 v1.1 原案）：SAQ job 默认 60s timeout 会炸（可行性分析 #8），改为续轮汇报。
- **run_gov_checks 支持 pollution**：该门禁是 `git ls-files | xargs` 管道，非纯 argv 可表达；v1 收敛为 surface-only（工具 description 明示）。
- **jsonschema 库做参数校验**：新增依赖不值当；手写枚举/区间/路径校验 + `ToolValidationError` 回填模型重试，占轮次预算。

## 如何验证

- 后端新测试 47 用例全过（testcontainers PG）：加密往返/掩码、llm_client 七类错误（MockTransport，含 SOCKS 代理环境 ImportError 包裹）、工具校验（路径穿越/profile 枚举/白名单形状/**AGENT_TEST_ENV 四键结构断言**）、API（掩码不落明文、PUT 留空不变更、审计行、409 ai_not_configured、会话隔离 404、非 admin 审批 403、假客户端完整轮次含 D7 密钥不落消息断言、T2 提案止轮）。
- agent 测试 1236 全过（saq_tasks 导入链零回归）；ruff 全绿；前端 tsc + 621 vitest 全过。
- 迁移经 conftest 空库 `alembic upgrade head` 真实执行（47 用例即证据）。
- **PR-Agent gate 越权发现已修复**（#509 首评）：`query_recent_audit_logs`/`get_settings_overview`
  镜像的均为 `require_admin` 端点，原实现对全员开放构成越权。修复=**双门禁**：工具 payload
  按会话用户角色裁剪（`allowed_tool_names`）+ 执行面校验（模型点名调用角色外工具同样拒绝）；
  新增 3 用例锁定（含「payload 过滤但执行未挡」的缺口回归——首版修复正是被测试抓出此洞）。
- 迁移 DDL 另经一次性 PG 容器真实执行验证（本地 conftest 为 `create_all` 直建，不覆盖迁移链；
  stamp 跳过 main 既有缺陷 #510 后 `i5j6→q9r8` 全链路执行，四表五索引核验）。

## 何时重议

- 真实 LLM 供应商 function calling 兼容性：上线时 `test-connection` 实测（无兜底，D2）。
- `.env.backend` 加 `AI_ASSISTANT_FERNET_KEY` + 预约重启窗口后功能才可用（部署步骤见计划 §6）。
- 流式输出 / 全局抽屉（v2 预留）；`search_docs` 轻量索引（质量不足时）。
