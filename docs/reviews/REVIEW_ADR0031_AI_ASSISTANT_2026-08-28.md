# 只读审核报告：ADR-0031 平台 AI 助手 & 实施计划

- 审核日期：2026-08-28
- 审核对象（只读，不修改）：
  - `docs/adr/ADR-0031-platform-ai-assistant.md`（v1.0，Proposed）
  - `docs/reviews/AI_ASSISTANT_PLAN_2026-08-27.md`
- 审核方式：对照实际代码库逐条核验引用基座、事实声明、关联 ADR 与内部一致性（覆盖执行基座、数据模型、鉴权/审计/限流、前端依赖、alembic 现状、域划界参照等 20+ 项）

---

## 一、结论摘要

两份文档质量高，事实引用与既有代码基座高度吻合，决策逻辑自洽、安全边界分级清晰。**无阻断性缺陷，可进入人工评审**。但存在 **3 处需作者在实现前澄清/修订**的实质问题（§二 H1/H2、§三 M6），以及若干表述澄清与行号级细节（§三 M1–M5）。

---

## 二、高优先级发现（需修订或澄清）

### H1. `run_agent_tests` 工具缺少 `AGENT_TEST_ENV` 环境注入 —— 与 run_gates 语义不一致

计划 §3.4 的 `run_agent_tests` 定义为：

```
{PY} -m pytest backend/agent/tests[/file] -q（与 run_gates.py:104 同款解释器约束）
```

但核验 `scripts/run_gates.py:103-107`，`agent-tests` 门禁**并非裸跑**，而是带 `AGENT_TEST_ENV`（`scripts/run_gates.py:35-40`）：

```python
AGENT_TEST_ENV = {
    "TESTING": "1",
    "JWT_SECRET_KEY": "ci-test-secret-key",
    "DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
    "TEST_DATABASE_URL": "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test",
}
```

其注释（`scripts/run_gates.py:31-33`）明确说明：**「部分 agent 测试模块 import 时会解析 DATABASE_URL，但不会真正连接；与全量 backend-test 保持一致的环境可避免收集期 RuntimeError」**。本机（控制面）未注入这些 env 时，部分 agent 测试可能因 `resolve_database_url` 解析失败在收集期报错（`AGENTS.md`：`core/env_source.resolve_database_url` 解析不到就 RuntimeError，无兜底默认）。计划声称「同款解释器约束」但未继承 env，存在**语义缺口**。

> 建议：明确该工具执行时继承 `run_gates.py` 的 `AGENT_TEST_ENV` 等价环境（或复用同一常量），并在测试矩阵补一条「未设 env 时收集期行为」用例。

### H2. alembic head 现状与计划的风险描述不符（且涉及一个未跟踪迁移文件）

计划 §0.5 与 §9#1、ADR 影响段均写「背景探查提示可能存在多 leaf」。实测当前 **alembic 为单 head：`k8l9m0n1o2p3`**，多 leaf 风险疑已收敛。但注意：`git status` 显示该 head 迁移文件 `backend/alembic/versions/k8l9m0n1o2p3_align_host_id_with_ip_after_subnet_migration.py` 处于**未跟踪（untracked）**状态。

> 建议：实现第一步仍按计划跑 `alembic heads` 复核（正确做法），但应在计划中把「可能存在多 leaf」从风险表降级为「已核实单 head，但 head 文件未跟踪、须确认其是否随 PR 合入后再定 down_revision」，否则新迁移的 `down_revision` 若指向一个未合入的 head，合入后可能产生分支/孤儿。

---

## 三、中等/低优先级发现

### M1. reload-config 路由本身非 admin，但工具置于 T2（admin 审批）——建议文档点明差异是**有意收紧**

计划 §3.4 将 `reload_agent_config` 列为 T2（默认 admin 审批）。核验 `backend/api/routes/dedup.py:451-468`，现有 `reload-config` 路由仅用 `get_current_active_user`（任何登录用户）即可触发。助手侧把该动作从「登录即可」提升到「admin 审批」，是**比现有路由更严**的边界，方向正确，但两份文档均未点明这一**收紧差异**。为避免评审时被质疑「与现有权限不一致」，建议在 ADR D1/T2 或实施计划 §3.4 补一句说明。

### M2. `settings` 路由「env 只读聚合」表述不精确

ADR D3 与计划 §3.4 `get_settings_overview` 说 settings 是「env 值的只读聚合」。核验 `backend/api/routes/settings.py:43-68`：平台名/时区来自 env，心跳/离线阈值来自模块常量，但**通知开关（`*_notification_enabled`）是查 `AlertRule` 表聚合的**。即既有 settings 是「env + 常量 + DB 聚合」的混合体，非纯 env。若计划把 `ai_assistant_config` 定位为「第一张平台级配置表」，需确认「settings 无 DB 先例」的表述只指**配置表**（可配置项落库）而非所有查询，避免与既有通知规则表（DB 存储）冲突。

### M3. `search_docs` 工具的实现域未在 ADR 侧体现

计划 §3.4 定义了 `search_docs`（docs/ 纯 Python 文件扫描，返回相对路径+行号），但 ADR 影响段（§影响 / §关联实现）未把该工具列为影响面。属低优先级，建议 ADR 影响段补一行，保持两份文档影响面一致。

### M4. 行号级漂移（不阻塞，仅修正引用）

- 计划 §0 引 `scripts/run_gates.py:147-151` 指 check:quick/pr 门禁矩阵 —— 实际 `PROFILES` dict 位于 `scripts/run_gates.py:146-155`，对应关系正确，行号略偏（1-4 行）。
- 计划 §3.4 引 `routes/notifications.py:146`（通道测试发送已存在）与 `:160`（`send_to_channel`）—— 实测 line 146 为 `test_channel` 路由装饰器、line 160 为 `send_to_channel(...)` 调用，**引用准确**，无需改动。
- 计划 §3.4 引 `run_gates.py:104` 同款解释器约束 —— line 104 确为 `agent-tests` 门禁（`f"{PY} -m pytest backend/agent/tests/ -q"`），引用准确。

### M5. 前端 `react-markdown` 依赖（低）

ADR 影响与计划 §4 均声明「前端新增 `react-markdown`（禁 rehype-raw）」。已核验前端现有依赖（`frontend/package.json`）不含 react-markdown，该声明成立；禁 rehype-raw 与 D7 注入防线一致，无问题，仅为记录。

### M6. `query_plan_runs` 的 `specialty?` 过滤参数与数据源字段不对齐（实现时注意）

计划 §3.4 T0 工具 `query_plan_runs` 参数含 `specialty?`，数据源标注为「`plan_run` 模型查询」。核验发现 **`specialty_id` 定义在 `plan` 模型（`backend/models/plan.py:56`，FK `specialty.id`），`plan_run` 模型没有该字段**（`plan_run.py` 有 `status`/`project_id`/`run_context`，无 specialty）。按 specialty 过滤 PlanRun 需经 `plan_run → plan` 关联 join，或计划本意是过滤 Plan 级字段。建议实现时明确过滤链路，避免「参数存在但查询字段缺失」的落空。

---

## 四、已核验为准确的关键事实（供评审参考）

以下引用经代码核验**准确无误**，构成计划/ADR 的可信基础：

| 声明 | 核验结果 |
|------|----------|
| RunConsole 基座（argv 子进程 + `console:{run_id}` 房间 + 进程组取消 + run_key 串行） | `backend/services/run_console.py`：`start()` 强制 argv 列表不走 shell（L159-238）、`_reader_loop` 用 `console:{run_id}` 房间（L245）、`cancel()` 进程组 kill（L321-358）、`_inflight_keys` 串行（L181-183） |
| jira-run 三件套消费范式（start/status/log/cancel） | `backend/api/routes/dedup.py`：`POST /runs`（L184）、`GET /runs`（L320）、`GET /runs/{console_run_id}/record`（L338）等，均为 RunConsole 消费 |
| `emit_agent_control` 下发 reload_config 路径 | `backend/api/routes/dedup.py:451-468`，`emit_agent_control(host_id, "reload_config")` |
| SAQ `SAQ_FUNCTIONS` 注册 + `enqueue_sync` | `backend/tasks/saq_worker.py`：`from backend.tasks.saq_tasks import SAQ_FUNCTIONS`（L24）、`enqueue_sync`（L259-343） |
| Fernet 凭据模式 + 仅 TESTING 兜底键 | `backend/core/ssh_security.py`：`_get_fernet()` 用 `SSH_CREDENTIALS_FERNET_KEY`，`TESTING==1` 用 `_TEST_FERNET_KEY`（L271-280），与计划 `ai_security` 克隆方案一致 |
| `send_to_channel` 通道测试已存在 | `backend/api/routes/notifications.py:157-160`（`test_channel` → `send_to_channel`） |
| `run_gates check:quick/pr` profile 存在 | `scripts/run_gates.py:146-155`（`PROFILES`） |
| settings 为只读聚合 | `backend/api/routes/settings.py:43-68`（env + 常量 + DB 通知开关，注意非纯 env，见 M2） |
| 关联 ADR 存在（0006/0008/0015） | 三份文件均在 `docs/adr/` 下，路径引用正确 |
| 域划界参照：synthesis 否决「headless Maintain 全自动闭环」 | `AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md` §3「刻意不追」第 3 项确含该否决，且重议条件「生产库与控制面分离部署后」与 ADR-0031 触发复议条件 #1 完全一致 |
| 限流中间件（ADR 影响段「既有鉴权依赖 + 限流中间件」） | `backend/core/limiter.py` 的 `RateLimitMiddleware`（300 req/min/IP），`backend/main.py:271` 注册，声明成立 |
| `record_audit` 复用（D6 审计） | `backend/core/audit.py:35-98`：`record_audit(db, *, action, resource_type, resource_id, details, user_id, username, request)`，含 savepoint 包裹与 `resource_id` 转字符串，签名与计划使用一致 |
| `ai_chat_session.user_id` FK `users` | `backend/models/user.py`：`__tablename__ = "users"` 存在，FK 可成立 |
| `ai_assistant_action.console_run_id` 可复用 RunConsole | `backend/models/jira_run.py` 有同款 `console_run_id` 列（jira-run 先例），模式复用成立 |
| T1 `run_gov_checks` 工具依赖的脚本 | `tools/dev/check_governance_surface.py` 与 `collapse-blank-pollution.py` 均存在，且 `run_gates.py:69-88` 以 `--check` 调用，与计划一致 |
| T0 `query_hosts`/`query_devices` 数据源 | `Host`/`Device` 模型存在；注意 **`Device` 定义在 `backend/models/host.py:52`**（非独立 `device.py`），`status`/`host_id` 字段齐全，可实现 |
| T0 `get_plan_run_detail` 的 `run_context` 摘要 | `backend/models/plan_run.py:49` `run_context` JSONB 列存在，可行 |

---

## 五、建议动作

1. **作者修订（合入评审前）**：
   - H1：明确 `run_agent_tests` 的 env 注入策略（继承 `AGENT_TEST_ENV` 或等价），并补测试。
   - H2：把 alembic 风险表述从「可能多 leaf」更新为「已核实单 head，但 head 文件未跟踪需确认」，或至少在实现前复核。
   - M1/M2：补两处边界/表述澄清。
   - M6：明确 `query_plan_runs` 按 specialty 过滤的关联链路。
2. **评审时关注**：D1 分级（尤其 T1 默认自动、T2 白名单）在真实运维场景下的爆炸半径假设；D7 注入防线中「工具参数过 schema 校验后由服务端模板拼 argv」的结构性防注入是否足够；`search_docs` 检索质量（计划已列为风险 #6，可接受）。

---

## 六、修订确认（2026-08-28，v1.1 复审）

作者已按本报告将两份文档修订至 v1.1，逐条复核结果：**全部 8 项发现均已处理，修复质量良好，无残留实质问题**。

| 发现 | 修复 | 复核结论 |
|------|------|----------|
| H1 | 计划 §3.4/§5：`run_agent_tests` 显式注入 `AGENT_TEST_ENV` 等价四键（`run_gates.py:35-41` 同源），并显式覆盖生产 `DATABASE_URL` 防 RunConsole 环境继承透传；补「工具执行环境」测试用例 | ✅ **超出建议**：防生产连接串透传与 AGENTS.md 生产库约束一致 |
| H2 | ADR 影响段 + 计划 §0.5/§3.1/§9#1 统一更新为「已核实单 head `k8l9m0n1o2p3`、文件未跟踪、`down_revision` 待其合入 main 后落位」 | ✅ 多 head 风险表述已消除 |
| M1 | ADR D1/T2 新增「权限收紧说明」；计划 §3.4 `reload_agent_config` 标注有意收紧（现有路由登录即可） | ✅ |
| M2 | ADR 背景#2/D3、计划 §3.4 统一为「env + 常量 + DB 聚合」混合体表述 | ✅ |
| M3 | ADR 影响段新增「工具面」行（含 `search_docs` 只读不建索引） | ✅ |
| M4 | 计划头部行号修正为 `run_gates.py:146-155` | ✅ |
| M5 | 修订说明确认核验无需改动 | ✅ |
| M6 | 计划 §3.4 `query_plan_runs` 明确 specialty 经 `plan_run ⨝ plan.specialty_id` 过滤（`plan.py:56`） | ✅ 与代码一致 |

**残留微小项（不影响正确性）**：计划 §3.4 引 `routes/dedup.py:453`（`get_current_active_user`），实际该依赖位于 454 行（453 为 `host_id` 参数行）——1 行级偏差，指向区域正确，可随手修正或忽略。

---

## 七、v1.2 复审（2026-08-28，载体选型评估落档）

ADR 更新至 v1.2：完成「LLM 载体选型（手写 httpx vs 框架族）」专项评估落档。核验结果：**新增事实声明全部准确，决策逻辑自洽，无新增问题**。

### 事实核验

| v1.2 新增声明 | 核验结果 |
|---------------|----------|
| 「httpx 已在运行时依赖（`requirements.txt:43`）」 | ✅ `backend/requirements.txt:43` 确为 `httpx>=0.28.1,<1.0` |
| 「`file_server_monitor.py` 先例」 | ✅ `backend/services/file_server_monitor.py:15` 为 `import httpx`，且是**运行时服务**（非 scripts/），用 httpx 查 Prometheus（`_PROMETHEUS_URL_DEFAULT`），确为运行时依赖用法先例 |
| 「全仓无 openai SDK」 | ✅ `import openai / litellm / langchain / pydantic_ai / pydanticai` 在 `backend/` 零匹配 |
| 「SDK 底层传输同为 httpx、MockTransport 注入测试依然成立」 | ✅ openai 官方 python SDK 基于 httpx 构建，论断成立 |

### 决策逻辑核验

1. **D2 新增「载体收敛 + 可逆性」**（L59）：载体收敛 `llm_client.py` 单模块、工具/编排/路由层不直接碰 HTTP，切换官方 SDK 成本 ≈ 重写单模块 + lock 重生成、不涉及其余层。逻辑闭环：上层仅消费归一化 `AssistantReply`，载体隔离成立。
2. **备选表扩为框架族对比**（L104-105），四条拒绝理由与既有决策逐条自洽：
   - openai 官方 SDK → v1 拒绝、**留作首选切换目标**（与触发条件 #6 呼应）；
   - LiteLLM → 多 provider 统一与 **D2 单协议裁决冲突**；
   - PydanticAI → 框架内工具循环与 **D1「T2 提案即止轮待审批」控制流冲突**（须 deferred 绕行），类型安全收益与既有 JSON Schema 校验重叠；
   - LangChain → 放大 agent 自主性与 **D4/D7「argv 服务端模板 + 收敛 LLM 自由度」取向相反**。
3. **触发复议条件 #6**（L128）：载体切换条件显式化（协议边角反复打补丁 / 流式复议通过 → 切官方 SDK；非 OpenAI 兼容供应方落地 → 评估 LiteLLM；LangChain 类在本 ADR 生命周期内不再议）——与 D2 可逆性说明闭环呼应。

### 结论

v1.2 是对「引入框架 vs 手写 httpx」这一悬而未决问题的**结构性收口**：评估基线核验准确、框架族拒绝理由与 D1/D2/D4/D7 决策逐条自洽、切换路径以触发条件显式化。**无阻断性问题，可确认通过**。与实施计划 v1.1（§3.3 手写 httpx 客户端）一致，无跨文档冲突。

---

*审核方式：只读，未对任何文档/代码做修改。*
