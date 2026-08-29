# ADR-0031: 平台 AI 助手（运维域 LLM 助手与风险分级自治边界）

- 状态：**Proposed（待评审，未实施）**
- 优先级：**P1**
- 目标里程碑：M8（建议，未排期）
- 日期：2026-08-27
- 决策者：平台研发组（待评审确认）
- 标签：AI 助手, LLM, 工具调用, 运维, 安全边界
- 实施计划：[`docs/reviews/AI_ASSISTANT_PLAN_2026-08-27.md`](../reviews/AI_ASSISTANT_PLAN_2026-08-27.md)
- 审核报告：[`docs/reviews/REVIEW_ADR0031_AI_ASSISTANT_2026-08-28.md`](../reviews/REVIEW_ADR0031_AI_ASSISTANT_2026-08-28.md)（只读核验；v1.1 按其确认结论修订）
- 导航与入口设计：[`docs/reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md`](../reviews/FRONTEND_NAV_IA_REDESIGN_2026-08-28.md)（v1.1 已评审裁决，AI 入口落位经 v1.3 回写本文）

## 修订记录

| 日期 | 版本 | 内容 |
|------|------|------|
| 2026-08-27 | v1.0 | 草案初稿，待人工评审 |
| 2026-08-28 | v1.1（评审修订） | 按只读审核报告逐条**独立复核确认后**修订：背景/D3 的 settings 表述精确化（「env + 常量 + DB 聚合」混合体，M2）；D1/T2 点明 reload_config 提为 admin 审批是**有意收紧**（现有路由登录即可，M1）；影响段 alembic 表述更新为「已核实单 head `k8l9m0n1o2p3`、head 文件未跟踪须确认合入」（H2）并补工具面一行（M3）。H1/M4/M6 属实施计划层，随计划 v1.1 修订 |
| 2026-08-28 | v1.2（载体选型评估落档） | 完成「LLM 支持引入框架/技术栈 vs 手写 httpx」专项评估并落档：D2 补载体隔离与可逆性说明；备选表将「引入 openai SDK」扩为框架族对比（官方 SDK 留作首选切换目标；LiteLLM/PydanticAI/LangChain 分别给出拒绝理由）；触发复议条件新增 #6 载体切换条件。评估基线：httpx 已在运行时依赖（`requirements.txt:43`，`file_server_monitor.py` 先例），全仓无 openai SDK |
| 2026-08-28 | v1.3（AI 入口落位回写） | 前端导航 IA 治理方案（FRONTEND_NAV_IA_REDESIGN，v1.1）经用户评审裁决：方案 A 保守档采纳、AI 入口=**侧边栏置顶 pinned 块**（v1）、全局抽屉留 v2；本文前端影响段据此回写，实施计划 §4 同步（v1.2）。PR 切分：导航治理 PR1 先行（不依赖本 ADR，可独立合入），AI 助手实现为 PR2 |
| 2026-08-29 | v1.4（二轮审核采纳 + 文档补齐） | 采纳二轮审核（H1 pending 收口/H3 跨循环 ack 下发/M1 枚举校验/M2 超时按 max_turns 估/M3 retries=0/M4 会话严格隔离/M5 后台任务持引用/Low-1 执行闸门收紧）；D7 措辞对齐实现（逐参手写校验，非 jsonschema）；§影响承诺的设计文档本批补齐（docs/design/2026-08-27-platform-ai-assistant.md）。低危索引名与 .env.example/.ts 注释随批修正 |

## 背景

为平台新增「AI 助手」功能：管理员配置 OpenAI 兼容的 API URL / API Key / 预设模型后，登录用户以对话方式让助手查询平台状态、运行测试与质量门禁、执行日常运维动作。服务对象为**运维端 / 平台管理员 / 平台使用者 / 平台开发者**四类角色——本功能定位是**运维域助手**（操作对象是平台运行态与机群），不是开发流程自动化工具。

现状基线（2026-08-27 探查结论，file:line 证据见实施计划 §3）：

1. 平台**无任何 LLM 集成**（`backend/` 内 openai/llm/chat_completion 等关键词仅命中文档），本 ADR 为从零建子系统。
2. 平台级**可编辑配置**无 DB 承载：`GET /api/v1/settings` 是「env + 模块常量 + DB 聚合（通知开关查 `AlertRule` 表）」的只读混合体——通知开关虽落库但属领域规则表，平台级连接/运行参数类**可编辑配置表**为零先例。
3. 可复用基座齐备：RunConsole（argv 子进程 + Socket.IO 房间日志 + 进程组取消，`services/run_console.py`，消费范式见 `/api/v1/jira/runs` 三件套）、SAQ 任务与 `SAQ_FUNCTIONS` 注册、Fernet 凭据加密（`core/ssh_security.py`）、`scripts/run_gates.py` 质量门禁入口、完整 REST API 面 + `record_audit` 审计。
4. 控制面所在机器**同时是生产库宿主机**（AGENTS.md 生产约束），部分运维动作爆炸半径大——这是自治边界必须分级的原因。

### 与 AI-Native SDLC 评审的关系（显式划界）

[`AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](../reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md) 否决的「headless Maintain 全自动闭环」针对**开发全流程域**（告警→诊断→自动修复代码的无人值守循环，操作对象是代码仓库与开发流程）。本 ADR 是**运维域**功能，不引用该否决作为约束；自治边界以运维风险分级独立裁决（D1）。

两域判据：助手动作的**落点**是「代码仓库 / 开发流程」属 SDLC 域（受该评审约束），是「平台运行态 / 机群 / 生产库」属运维域（受本 ADR 约束）。**任一方试图越界（例如在本助手框架内做开发自动化，或在 SDLC 工具链里直接操作机群），即触发对方评审复议。**

## 决策

### D1：自治边界 = 运维风险四级（T0–T3）

自治程度按动作的**运维风险**分级，与用户角色解耦（角色只决定谁能审批，见 D6）：

| 层级 | 内容 | 自治策略 |
|------|------|----------|
| **T0 观测诊断** | 健康/指标、PlanRun/Job/Host/设备查询、审计日志检索、docs/ 文档检索 | **自动执行**，无需确认 |
| **T1 测试与门禁** | `run_gates check:quick/check:pr`、agent 测试套件、指定测试文件（均为**非破坏性**、控制面本机、不触生产库） | **默认自动执行**；管理员可通过全局开关收回为需确认 |
| **T2 运维动作** | 脚本目录扫描、通知通道测试发送、Agent reload_config 下发 | **默认需 admin 审批**（对话内操作卡）；管理员可把个别低危工具加入 `auto_approve_tools` 免确认白名单（仅 T2 级工具可加入） |

> 权限收紧说明（M1）：现有 `POST /hosts/{host_id}/reload-config` 路由为登录即可触发（`routes/dedup.py:453`，`get_current_active_user`）；助手侧将 reload_config 提为 admin 审批是**有意收紧**而非与现状对齐——助手是无人值守触发源，暴露面大于人工页面。
| **T3 硬排除** | host hot-update、生产库写操作、任意 shell / 自由拼装命令 | **不可配置、不提供白名单入口**；助手只能给出生成指引，不能代执行 |

分级依据：T1 只消耗控制面本机算力且可重复执行（与人工 SSH 跑 `run_gates` 等价），逐次人审只增加等待不降低风险；T2 改变平台或机群状态；T3 涉及生产库或机群级重启。T1 收回开关与 T2 白名单都落库（D3），是管理员对自动化的**运行时调节旋钮**，不是代码开关。

### D2：单一 OpenAI 兼容协议

- 配置三元组：`base_url` / `api_key` / `model`；调用 `{base_url}/chat/completions`，**必须支持 function calling（tools）**；v1 非流式。
- 不适配 Anthropic 原生等非 OpenAI 兼容协议；出现此类供应方需求时引入协议适配层（见触发复议条件）。
- 供应商差异（function calling 支持度、错误形状）由 `test-connection` 预检暴露，不做多供应商兜底。
- LLM 载体收敛在 `services/ai_assistant/llm_client.py` 单模块（手写 httpx，运行时依赖已有，`requirements.txt:43`、`file_server_monitor.py` 先例），工具/编排/路由层不直接碰 HTTP——载体选型是**可逆决策**：切换官方 SDK 的成本 ≈ 重写单模块 + lock 重生成，不涉及其余层（切换触发条件见「触发复议条件」#6）。

### D3：配置落库与凭据加密（平台级运行时配置 DB 化第一例）

- 新增 `ai_assistant_config` 单行表承载 D2 三元组 + D1 两个旋钮 + 编排参数（temperature / max_turns / request_timeout_seconds）。既有 settings 路由是「env + 常量 + DB 聚合」的只读混合体（可编辑配置无 DB 承载），此为第一张平台级可编辑配置表——**先例意义仅限本表**，其他配置是否 DB 化仍须各自评审，不自动推广。
- `api_key` 以 Fernet 加密落库，克隆 [`core/ssh_security.py`](../../backend/core/ssh_security.py) 模式：独立 env 键 `AI_ASSISTANT_FERNET_KEY`（与 `SSH_CREDENTIALS_FERNET_KEY` 分离，不混用密钥域），仅 `TESTING=1` 允许测试兜底键。
- 配置 API **永不回明文 Key**：GET 回掩码（保留末 4 位），PUT 留空 = 不变更。未配置时功能降级（结构化错误码），不阻塞平台其他功能，故不设 lifespan fail-fast。

### D4：执行通道收敛（不建第二执行通道）

- **长命令一律 RunConsole**：argv 由服务端白名单模板拼装（工具注册表内每工具一个 argv builder），**禁止 `shell=True`、禁止任何 LLM 输出直接拼接进 argv**；run_key 串行化防并发；日志落盘可回放；取消走进程组 kill。完全复用 jira-run 消费范式（start / status / log / cancel 四端点）。
- **观测类查询走服务层直读**（sync Session 查既有模型），不经 HTTP 自调。
- T1 工具的测试文件参数必须解析后位于 `backend/agent/tests/` 等白名单目录内（防路径穿越），`run_gates` 的 profile 参数是有限枚举。

### D5：编排（SAQ 轮次循环）与实时（轮询为主）

- 每轮对话是一个 SAQ 任务（注册进 `SAQ_FUNCTIONS`，job key 按 session 串行防并发轮）：加载历史 → 调 LLM → T0/T1 工具直接执行并回填 tool message → T2 工具创建 `proposed` 状态 action 记录后**立即止轮**等待审批 → 审批执行完成后 enqueue 续轮把结果喂回模型。`max_turns` 上限防失控循环。
- 前端 v1 以轮询为主（pending 时 2s refetchInterval）；Socket.IO 沿用既有 `console:{run_id}` 房间供长命令日志实时跟随，**不新增** SSE / 流式 token 通道（遵循 ADR-0006 的 REST + WebSocket 分工）。

### D6：角色、权限与审计

| 能力 | 权限 |
|------|------|
| 对话（会话/消息） | 所有登录用户（会话按用户隔离，只能见自己的） |
| T0/T1 工具 | 所有登录用户 |
| T2 工具提案 | 所有登录用户；**审批/拒绝仅 admin** |
| 助手配置 CRUD / test-connection / 旋钮调节 | admin |
| 审计 | config 变更、action 提案/批准/拒绝、执行开始/结束/取消全量 `record_audit`（ADR-0015），动作留痕含 params 摘要与审批人 |

### D7：prompt 注入防线（硬红线）

- system prompt 注入平台上下文与边界声明（回答用中文、工具边界、操作必须走 T2 流程）。
- **API Key 与任何凭据永不进 prompt、日志、审计详情与 LLM 上下文**。
- 工具参数按各工具声明逐参手写校验（枚举/区间/路径白名单/标识符卫生——有意不引 jsonschema 库，见 Agent Note 取舍），校验失败即向模型报错重试（占轮次预算），不做静默修正；argv 只能来自服务端模板（D4），从结构上使「诱导助手执行任意命令」不可表达。

## 备选方案与权衡

| 方案 | 结论 | 理由 |
|------|------|------|
| 全自动无边界 | 拒绝 | T3 动作（hot-update/生产库）爆炸半径大，失控代价不可逆 |
| 一刀切确认制（所有副作用动作逐次人审） | 拒绝 | T1 非破坏性且等价于人工操作，逐次确认只制造等待，削弱「跑测试」核心体验；风险旋钮交给管理员（D1） |
| 纯顾问模式（只建议不执行） | 拒绝 | 不满足「让助手跑测试、做日常运维」的功能诉求 |
| MCP 协议接入 | v1 拒绝 | 全新集成面；OpenAI function calling 已满足工具调用需求；仓库 AI-Native 评审已有「暂不 pursuing MCP 部署分层」记录 |
| SSE 流式输出 | v1 拒绝 | 全仓无 SSE 先例，ADR-0006 已确立 REST + Socket.IO 分工；轮询满足 v1 体验，流式留待真实体验瓶颈出现 |
| env 存配置 | 拒绝 | 无法 UI 化、改配置需登生产机、无法承载逐工具白名单这类结构化配置 |
| 引入 openai 官方 SDK | v1 拒绝，**留作首选切换目标** | 新增依赖 + `requirements.lock` 重生成（py3.11）；SDK 唯一实际对价是协议边角处理（兼容网关错误形状、内建重试）——v1 单端点非流式用不上，且其底层传输同为 httpx、MockTransport 注入测试依然成立；触发条件出现再切换，迁移面 = 单模块 |
| LiteLLM / PydanticAI / LangChain 等 agent 框架 | 拒绝 | LiteLLM 的核心价值（多 provider 统一）与 D2 单协议裁决冲突，为未触发的重议条件预付重依赖与版本 churn；PydanticAI 的框架内工具循环与 D1「T2 提案即止轮待审批」控制流冲突，须以 deferred 机制绕行，类型安全收益与既有 JSON Schema 校验重叠；LangChain 类框架的价值在放大 agent 自主性，与 D4/D7「argv 服务端模板 + 结构性压缩 LLM 自由度」取向相反，依赖树与调试黑盒成本最高 |

## 影响

- **DB**：新增 `ai_assistant_config` / `ai_chat_session` / `ai_chat_message` / `ai_assistant_action` 四表，additive migration（ADR-0008）；alembic 已核实**单 head** `k8l9m0n1o2p3`（2026-08-28 时该 head 文件尚为未跟踪状态）——新迁移的 `down_revision` 须待该 head 随所属变更合入 main 后落位，避免产生孤儿迁移。
- **API**：新增 `/api/v1/ai-assistant` 路由组约 13 端点（清单见实施计划 §3.6），全部走 `ApiResponse` 信封 + 既有鉴权依赖 + 限流中间件。
- **工具面**：新增 14 个工具（T0×8 / T1×3 / T2×3），其中 `search_docs` 对 `docs/` 目录只读检索（文件名 + 内容行摘录），不新增存储、不建索引（M3）。
- **依赖**：后端**零新增** Python 依赖（httpx 已有）；前端新增 `react-markdown`（禁 rehype-raw，防 HTML 注入）。
- **安全**：新增出站流量（对所配 LLM API）；`.env.backend` 新增 `AI_ASSISTANT_FERNET_KEY`；密钥掩码与 D7 红线由测试矩阵锁定。
- **前端**：`/assistant` 独立页 + admin 设置页；AI 入口为**侧边栏置顶 pinned 块**（v1.3 落定，见导航治理方案 FRONTEND_NAV_IA_REDESIGN §5.1：横切入口不占业务组坑位，collapsed/移动端分支与 aria-label 注意点在该节）；对话组件按可复用方式拆分，v2 预留 Header 悬浮按钮唤起全局抽屉。
- **文档**：实施 PR 附设计文档 `docs/design/2026-08-27-platform-ai-assistant.md` + Agent Note + DOC-MAP 登记。

## 落地与后续动作

分两阶段：阶段一 = 本 ADR + 实施计划文档人工评审（当前）；阶段二 = 实现 PR（后端 + 前端 + 测试 + 文档，详见实施计划 §2）。

**触发复议条件**（未触发前不得重提）：

1. **生产库与控制面分离** → 可上调 T2 自动化层级、重审 T3 排除范围；
2. 出现**非 OpenAI 兼容供应方**刚需 → 引入协议适配层；
3. hot-update 类高危动作确需助手代执行 → 必须另开 ADR 论证（本 ADR 的 T3 不提供配置化入口，堵住「白名单渐进扩权」的暗门）;
4. 多租户 / 每用户独立模型配置需求出现；
5. 在本助手框架内做**开发流程自动化**（改代码、提交 PR）→ 属 SDLC 域越界，触发 AI-Native SDLC 评审复议。
6. **LLM 载体切换**：实测兼容网关反复出现协议边角问题（错误形状解析持续打补丁）或流式输出复议通过 → 切 OpenAI 官方 SDK（迁移面 = `llm_client.py` 单模块）；非 OpenAI 兼容供应方落地时评估 LiteLLM；LangChain 类重型框架在本 ADR 生命周期内不再议。

## 关联实现/文档

- 实施计划：[`docs/reviews/AI_ASSISTANT_PLAN_2026-08-27.md`](../reviews/AI_ASSISTANT_PLAN_2026-08-27.md)（数据模型 / 工具清单 / 路由 / 测试矩阵 / 部署步骤）
- 域划界参照：[`docs/reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md`](../reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md)
- [ADR-0006](./ADR-0006-realtime-communication-rest-plus-websocket.md)（REST + WebSocket 分工，D5 依据）
- [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md)（迁移治理）
- [ADR-0015](./ADR-0015-audit-log-system.md)（审计）
- [AGENTS.md](../../AGENTS.md)（生产库约束——T1 工具不触生产库的边界依据）
- 复用基座：`backend/services/run_console.py`、`backend/tasks/saq_worker.py`、`backend/core/ssh_security.py`、`scripts/run_gates.py`
