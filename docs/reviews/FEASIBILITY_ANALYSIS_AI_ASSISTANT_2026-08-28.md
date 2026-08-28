# 功能需求总结与可行性分析：平台 AI 助手（ADR-0031 / 实施计划 v1.1）

- 日期：2026-08-28
- 依据：`docs/adr/ADR-0031-platform-ai-assistant.md`（v1.1）、`docs/reviews/AI_ASSISTANT_PLAN_2026-08-27.md`（v1.1）、`docs/reviews/REVIEW_ADR0031_AI_ASSISTANT_2026-08-28.md`
- 方法：需求逐条映射到既有代码基座（核验过的 file:line 证据），按「复用成熟度 / 实施复杂度 / 风险敞口」三维评级

---

## 一、功能需求总结

### 1. 产品定位

为平台新增「AI 助手」：管理员配置 OpenAI 兼容 API（`base_url` / `api_key` / `model`）后，登录用户以对话方式让助手**查询平台状态、运行测试与质量门禁、执行日常低危运维动作**。定位为**运维域助手**（操作对象=平台运行态与机群），不是开发流程自动化工具（与 AI-Native SDLC 评审域划界）。

**服务角色**：运维端/平台管理员（全能力）、平台使用者（对话 + T0/T1 + T2 提案）、平台开发者（同使用者，T1 结果辅助自检）。

### 2. 功能清单（按自治层级）

| 层级 | 功能 | 工具/能力 | 自治策略 |
|------|------|-----------|----------|
| **T0 观测诊断**（8 工具） | 平台健康直读、PlanRun 列表/详情、Host 查询、Device 查询、审计日志检索、docs/ 文档检索、settings 聚合 | `get_platform_health` `query_plan_runs` `get_plan_run_detail` `query_hosts` `query_devices` `query_recent_audit_logs` `search_docs` `get_settings_overview` | 自动执行，无需确认 |
| **T1 测试与门禁**（3 工具） | `run_gates check:quick/pr`、agent 测试套件（含单文件）、治理面检查（surface/pollution） | `run_quality_gate` `run_agent_tests` `run_gov_checks` | 默认自动；管理员可全局收回（`t1_require_confirm`） |
| **T2 运维动作**（3 工具） | 脚本目录扫描、通知通道测试发送、Agent reload_config 下发 | `scan_script_catalog` `test_notification_channel` `reload_agent_config` | 默认 admin 审批（对话内操作卡）；低危可入 `auto_approve_tools` 白名单 |
| **T3 硬排除** | host hot-update、生产库写、任意 shell | 不注册工具、无白名单入口 | 只给指引不代执行 |

### 3. 支撑性功能需求

| 面 | 需求 |
|----|------|
| **配置** | 平台级运行时配置 DB 化第一例：`ai_assistant_config` 单行表（D2 三元组 + `temperature`/`max_turns`/`request_timeout_seconds` + T1 收回开关 + T2 白名单）；Fernet 加密 Key（`AI_ASSISTANT_FERNET_KEY`）；GET 掩码（末 4 位）、PUT 留空=不变更、`test-connection` 预检 |
| **对话编排** | 每轮=一个 SAQ 任务（`ai_assistant_turn_task`，job key `ai-turn:{session_id}` 串行）；载历史→组 system prompt→调 LLM→T0/T1 直执行回填 / T2 提案止轮→审批后续轮；`max_turns` 上限；失败落 `message.status=failed` 不静默重试 |
| **执行通道** | 长命令一律 RunConsole（argv 服务端模板拼装、禁 `shell=True`、run_key 串行、进程组取消、日志落盘可回放）；观测查询走服务层直读不经 HTTP 自调 |
| **审批流** | `ai_assistant_action` 状态机（proposed→approved/rejected→running→succeeded/failed/cancelled）；T2 全量 `record_audit`；log 镜像 jira-run 读取 |
| **前端** | `/assistant` 独立页（左会话列表+右消息区，子组件 MessageBubble/ActionCard/LogPanel）；`/settings/ai-assistant` admin 设置页；`react-markdown`+`remark-gfm` 渲染（禁 rehype-raw）；pending/running 时 2s 轮询；样式走 design tokens |
| **数据模型** | 4 张新表：`ai_assistant_config` / `ai_chat_session` / `ai_chat_message` / `ai_assistant_action`；1 个 additive 迁移（ADR-0008） |
| **API** | `/api/v1/ai-assistant` 约 13 端点（config 读写/test-connection、sessions CRUD+消息、actions 详情/approve/reject/log/cancel）；未配置统一 409 `ai_not_configured` |
| **安全红线（D7）** | API Key 永不进 prompt/日志/审计/LLM 上下文；工具参数过 JSON Schema 校验失败即报错占轮次；argv 只来自服务端模板（结构性防注入） |

### 4. 明确不做（本期）

流式输出（SSE/token 通道）、hot-update、任意 shell、MCP、多租户、非 OpenAI 兼容协议、前端全局抽屉、需 PG 的 `backend/tests/` 代跑、前端 tsc/build 工具。

---

## 二、可行性分析

### 总评

**整体可行性高**。该方案是典型的「既有成熟基座的组合装配」而非从零造轮子：执行通道、任务编排、凭据加密、命令消费范式、质量门禁入口全部已存在且有生产先例，后端零新增依赖。主要风险集中在**外部依赖（LLM 供应商）**与**运行时环境（生产库同机约束）**，均有明确的缓解措施。

### 1. 需求×基座映射与可行性评级

| 功能需求 | 核验过的基座证据 | 可行性 | 说明 |
|----------|------------------|--------|------|
| RunConsole 长命令执行 | `services/run_console.py`：argv 不走 shell（L159-238）、`console:{run_id}` 房间（L245）、进程组取消（L321-358）、run_key 串行（L181-183） | **高** | 完整复用，jira-run 已生产验证 |
| jira-run 消费范式（start/status/log/cancel） | `api/routes/dedup.py`：`POST/GET /runs`（L184/320）、record（L338） | **高** | 四个端点模式可直接镜像为 action 端点 |
| SAQ 任务编排 | `tasks/saq_worker.py`：`SAQ_FUNCTIONS`（L24）、`enqueue_sync`（L259-343） | **高** | 注册新任务 + 串行 key 即可；注意 worker 并发与长任务超时需配 |
| Fernet 凭据加密 | `core/ssh_security.py`：`_get_fernet()`（L271-280） | **高** | 克隆模式 + 独立 env 键即可 |
| `run_gates` / 治理脚本 | `scripts/run_gates.py` PROFILES（L146-155）、`tools/dev/` 两脚本 | **高** | 直接作为 T1 argv 模板 |
| `reload_config` 下发 | `api/routes/dedup.py:451-468` `emit_agent_control` | **高** | 复用现成下发路径，仅加审批层 |
| 通知通道测试 | `api/routes/notifications.py:157-160` `send_to_channel` | **高** | 复用同路径 |
| 观测类查询数据源 | `plan_run`/`plan`/`host`/`device`/`audit_logs`/`users` 模型全部核验存在 | **高** | 直读既有模型；`query_plan_runs` 的 specialty 需 `plan_run ⨝ plan` 关联（v1.1 已明确） |
| 迁移 | alembic 单 head `k8l9m0n1o2p3`（文件未跟踪） | **中** | `down_revision` 时序依赖该 head 合入 main，需协调 |
| LLM 客户端（httpx 手写 OpenAI 兼容） | httpx 已在运行时依赖；零新增 | **中-高** | 代码量小但**供应商 function calling 兼容性需 test-connection 实测**（无兜底） |
| 前端两页 + react-markdown | vitest/jsdom、design tokens、NotificationsPage 测试范式 | **高** | 新增 1 个前端依赖；轮询模式成熟 |
| T2 审批流状态机 | jira_run 表 + record_audit（`core/audit.py:35-98`）先例 | **高** | 状态机是纯新代码，但范式清晰 |

### 2. 风险与缓解（按敞口排序）

| # | 风险 | 敞口 | 缓解 |
|---|------|------|------|
| 1 | **供应商 function calling 兼容性**（GLM/DeepSeek/Qwen 差异、错误形状） | 外部依赖、不可控 | `test-connection` 预检实测；不支持则明确报错不静默降级（D2） |
| 2 | **控制面即生产库宿主机**：T1 工具跑在本机、agent 测试受工作树/环境影响；`DATABASE_URL` 可能经环境继承指向生产库 | 生产安全 | `AGENT_TEST_ENV` 四键显式注入 + **显式覆盖生产 `DATABASE_URL`**（v1.1 已强化）；`backend/tests/` 不进 T1；操作卡展示 cwd 与工作树状态 |
| 3 | **alembic head 未跟踪**：`down_revision` 指向未合入 head 会产孤儿迁移 | 交付时序 | 实现第一步复核 `alembic heads`；确认 head 合入 main 后再落位（H2 已修订） |
| 4 | **LLM 幻觉/注入**：诱导执行任意命令、参数幻觉 | 安全 | D4 argv 模板结构性防注入（LLM 只能填参数）+ D7 JSON Schema 校验 + 注入样例测试锁定 |
| 5 | **T2 白名单渐进扩权** | 治理 | T3 不提供配置入口；白名单仅 T2 级工具可加入；触发复议条件 #3 堵暗门 |
| 6 | **会话历史膨胀** | 成本 | 单轮载入截断（最近 N 条 + token 预算）；全量留库可回看 |
| 7 | **`search_docs` 检索质量**（无向量索引） | 体验 | v1 文件名+内容摘录够用；质量不足再议轻量索引 |
| 8 | **SAQ 长任务超时/并发**：T1 最长 1800s，`SAQ_CONCURRENCY=10` | 运行时 | 需在实现时确认 `run_quality_gate`（pr 1800s）不超 SAQ job timeout 上限，必要时提高 timeout 参数 |

### 3. 前置条件与依赖

1. **head 迁移合入**：`k8l9m0n1o2p3` 所属变更合入 main（决定新迁移 `down_revision`）。
2. **LLM 端点可用性**：选型端点须实测支持 function calling（test-connection 验证）。
3. **`.env.backend` 新增 `AI_ASSISTANT_FERNET_KEY`**（部署步骤 §6.1，勿入 git）。
4. **backend 重启窗口**：本机即控制面，需预约窗口（real device safety）。
5. **前端 react-markdown 依赖**：单依赖新增，需过 Dependabot 与 lint 门禁。

### 4. 实施规模评估

| 维度 | 评估 |
|------|------|
| 后端新增模块 | 5 个（模型/ai_security/llm_client/tools/orchestrator）+ 1 路由文件 |
| 前端 | 2 页 + 1 API 层 + types/queryKeys 增量 |
| 测试 | 单测（无 PG）+ API 集成（testcontainers）+ 前端 vitest，矩阵见计划 §5 |
| 迁移 | 1 个 additive |
| 预计单 PR 规模 | 中等偏大（后端+前端+迁移+测试+文档），但均为组装式开发、无算法/协议攻坚 |

---

## 三、结论

1. **功能需求明确、边界清晰**：T0/T1/T2/T3 四级划分合理，与「控制面即生产库」的部署现实严格对齐；T3 硬排除 + T2 白名单 + T1 收回开关构成完整治理闭环。
2. **可行性高**：全部关键基座（RunConsole/SAQ/Fernet/jira-run 范式/run_gates/审计/模型）已核验存在且生产可用，后端零新增依赖，风险点均有明确缓解。
3. **需实现时重点把关**：① 供应商 function calling 实测；② alembic head 合入时序；③ SAQ job timeout 与 T1 长任务匹配；④ 生产 `DATABASE_URL` 透传防护（v1.1 已设计，实现时须落地为测试锁定的行为）。

---

*本分析基于 v1.1 文档与 2026-08-28 代码核验，只读，未修改任何文档/代码。*
