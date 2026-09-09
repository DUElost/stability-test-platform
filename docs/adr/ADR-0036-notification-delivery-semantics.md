# ADR-0036：通知投递语义契约（Notification Delivery Semantics Contract）

- 状态：**Proposed**
- 版本记录：v0.1（2026-09-08 初版草案，R11 审查触发）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-08
- 决策者：平台研发组
- 标签：通知, 投递语义, 异步队列, 重试, 幂等, 超时, 契约
- 关联：R11 台账 [#1125](https://github.com/DUElost/stability-test-platform/issues/1125)（R11-F09 #1117 / R11-F12 #1120 / R11-F15 #1122）；ADR-0011（通知什么，本文定投递行为）；ADR-0018（SAQ 基础设施与异步队列化方向）；ADR-0026（有界队列/背压先例）；ADR-0027（多实例守卫）；ADR-0025 D6（真实值班通道接入，挂起项触发条件）

## 1. 背景

### 1.1 问题定性：不是三个 bug，是一个未定义的语义

R11 审查（基线 `a0637a41`）把三件事列在同一主题下：

| 台账 | Issue | 现象 |
|---|---|---|
| R11-F09 | #1117 | 通知失败不会触发 SAQ 重试 |
| R11-F12 | #1120 | 钉钉业务失败可能被报告为发送成功 |
| R11-F15 | #1122 | SMTP 无 timeout + 通知线程池无界 |

三者各自都含可独立修复的缺陷，但它们共同暴露的是**同一件从未被定义的东西**：一次通知投递到底怎样算成功、失败由谁重试、重试何时停止、投递事实存在哪里。

### 1.2 事实核验（2026-09-08 静态核验）

**投递路径三套并存**：

- `dispatch_notification_async` → 共享后台线程池 fire-and-forget（`backend/services/notification_service.py:249-252`）——PlanRun 终态（`backend/services/plan_run_aggregation.py:68`）、RISK_HIGH（`:119`）、DEVICE_OFFLINE（`backend/api/routes/heartbeat.py:154`）走此路；
- SAQ 任务 `send_notification_task`（`backend/tasks/saq_tasks.py:55-72`，注册于 `:682`）——仅 recycler 补投路径 `_fill_deferred_post_completions` 使用（`backend/scheduler/recycler.py:750-765`），且带 `retries=3`；
- 同步直调 `send_to_channel`——管理员通道测试（`backend/api/routes/notifications.py:157-161`）与 AI 助手工具（`backend/services/ai_assistant/orchestrator.py:466-477`）。

**失败被吞**：通道级 `except Exception → logger.warning`，外层 `except Exception → logger.exception` 均不 raise（`notification_service.py:236-246`）。SAQ 只有在 `asyncio.to_thread` 抛出时才重试（`saq_tasks.py:67-71`），故声明中的重试在真实路径上收不到异常。现有重试测试 mock 掉 dispatcher（`backend/tests/tasks/test_saq_tasks.py:68-78`），绕过真实吞异常逻辑。

**"成功"口径不一致**：Webhook/DingTalk 仅 `raise_for_status()`（`:101-106`、`:136-137`），不解析业务响应；SMTP 无 timeout（`:151-156`）。测试接口把"未抛异常"直接当成功返回 `"Test notification sent"`（`notifications.py:160-161`），前端提示"测试通知已发送"（`frontend/src/pages/notifications/NotificationsPage.tsx:173`）。

**投递事实无处可查**：`NotificationLog` 只有 `source/event_type/severity/title/message/context/read/created_at`（`backend/models/notification.py:57-70`），无投递状态列；与 `NotificationChannel` 之间无外键或关联表，"这条通知投过哪些渠道、各通道结果如何"无法表达。全仓通知投递指标为 0（`backend/core/metrics.py` 无命中）。

**队列无界**：`ThreadPoolExecutor(max_workers=8)` 只限线程数，待提交队列无界（`backend/core/thread_pool.py:15`）。

### 1.3 触发条件：实现已静默改写既有决策

ADR-0018 Phase 2 明写「在 API 路由中将同步 dispatch 调用改为 `await queue.enqueue(...)`」（`docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md:230`），`send_notification_task` 亦为其交付项。实现却让三条主力路径退回线程池，且 SAQ 单层重试**本身也不满足** #1117 的验收（"不重发已成功通道"）：SAQ 重试是同一 job、同一 key、同一 kwargs 重跑（`saq.job.Job.retryable` = `retries > attempts`），一次重试会重发全部通道。

**这就是本文的立项理由**：防止实现再次悄悄创造一个与 ADR-0018 及本 ADR 不一致的投递语义。定义语义是 ADR 的职责；在语义缺位时，实现只能各自发明，且必然互相矛盾。

## 2. 决策

**建立通知投递语义契约。** 本文只裁决语义，不裁决实现参数。

### 2.1 投递管道（契约对象）

```text
Notification Event
      ↓
Dispatch Request
      ↓
Channel Adapter
      ↓
DeliveryResult
      ↓
Retry / Final State
```

契约约束的是这条链上每一步的**语义**，不是承载它的线程、队列或参数。

### 2.2 裁决清单

| # | 裁决 | 结论 |
|---|---|---|
| D1 | success 定义 | 归一化为 `ACCEPTED`；**ACCEPTED ≠ DELIVERED** |
| D2 | failure 定义 | 三态分类：`REJECTED_PERMANENT` / `REJECTED_TRANSIENT` / `UNKNOWN` |
| D3 | timeout | 契约层强制"每次网络投递必须有显式 deadline" |
| D4 | retry owner | **唯一**：异步队列（SAQ），且须投递级幂等 |
| D5 | retry 终止 | 统一策略对象：可重试性 + 退避 + 上限；UNKNOWN 纳入 |
| D6 | source of truth | 投递结果必须落在**业务事实层**（DB） |
| D7 | idempotency | at-least-once + 每通道去重键；**不承诺端到端去重** |
| D8 | sync/async 边界 | 同步仅限管理员通道连通性测试；生产路径一律异步 |
| D9 | channel adapter 语义 | 新渠道必须归一化为 `DeliveryResult` 并遵守本文 |

#### D1 — success 的定义

- 所有渠道适配器必须把底层结果归一化为项目统一的 **`DeliveryResult`**；
- **`ACCEPTED` = 渠道明确接受该投递请求**；
- **`ACCEPTED` 只表示"请求被接受"，不表示"用户已收到"**；
- 本文**不写**具体协议状态码/响应码。协议码属于 adapter contract 与实现注释，不属 ADR 正文——否则每接一个渠道都要改 ADR。

#### D2 — failure 的定义（三态）

| 结果 | 语义 | 重试 |
|---|---|---|
| `ACCEPTED` | 渠道明确接受请求 | 否（该次尝试终结） |
| `REJECTED_PERMANENT` | 渠道明确拒绝且再试无意义（配置/鉴权/语义错） | 否 |
| `REJECTED_TRANSIENT` | 明确瞬时失败（服务端 5xx、限流、连接失败） | 是（按策略） |
| `UNKNOWN` | **响应丢失/超时等，请求可能已成功** | 按策略（含退避与上限） |

**`UNKNOWN` 不恒等于 `REJECTED_TRANSIENT`**：二者必须可区分，因为"请求可能已成功"直接改变幂等与重复投递的语义。`UNKNOWN` 走同一套重试策略机制（退避 + 上限），但必须在投递记录中与瞬时失败区分保留。

#### D3 — timeout

- **契约层约束**：任何一次网络投递都必须有显式 deadline；"无 timeout 的网络调用"违反本文；
- **具体数值属实现/配置**（逐通道，可配置）。本文不规定 10s 还是 15s。

#### D4 — retry owner

- **重试由异步队列（SAQ）唯一负责**。调用方不得自行实现重试；fire-and-forget 线程池不得承担重试语义；
- **附加硬约束（否则 #1117 的验收无法满足）**：队列重试会整任务重跑，因此**投递层必须幂等**——重试不得重发已 `ACCEPTED` 的通道。重试 owner 与投递幂等必须成对成立，缺一不可。

#### D5 — retry 策略与终止

- 可重试性、退避曲线、重试上限构成**统一的策略对象**，逐通道可配；
- **`UNKNOWN` 与 `REJECTED_TRANSIENT` 纳入同一策略的退避与上限**，不得形成无上限重试源；
- **具体次数与退避参数属实现/配置**。本文只裁决"必须有上限、必须统一、UNKNOWN 必须计入"。

#### D6 — source of truth

- 投递结果必须存在于**业务事实层**（DB），不得只存在于 Redis/SAQ 的 job 生命周期中；
- 至少能表达：`requested` → `dispatched` → `accepted` / `retrying` → `failed` / `exhausted`；
- 状态词表**向前兼容**：未来新增 `delivered`（见 §4 挂起项）不得要求改本契约；
- 一个 Notification 天然对应 N 个通道投递尝试，模型必须能表达 1:N；
- **具体落库形态（扩 `notification_logs` 还是新建投递表）属实现设计**，本文不选死。

#### D7 — idempotency

- 契约保证 **at-least-once**（每通道至少一次投递尝试），**不承诺 exactly-once**；
- 去重键必须**与投递结果无关**（不能依赖"上次是否成功"来决定能否重试），因此每通道需要独立的去重键；
- **明确不承诺端到端去重**：外部通道未必支持去重，`UNKNOWN` 重试导致重复投递是该模型的已知代价，必须成文而非默认假设不存在。

#### D8 — sync vs async 边界

- **同步投递仅限**管理员的通道连通性测试（`POST /notifications/channels/{id}/test` 类）；
- **所有生产投递路径一律异步**，不得新增同步直投；
- 同步路径必须同样遵守 D1–D3（归一化结果、三态失败、显式 deadline），但不得自行重试。

#### D9 — channel adapter 语义

- 所有渠道（含未来飞书/企微/Slack 等）实现同一 `DeliveryResult` 与同一失败分类；
- **新增渠道不得自行定义"成功"**；
- 新渠道接入必须声明：超时、失败分类映射、重试策略、去重键形态。

### 2.3 挂起项（各有复议触发条件，未触发前不得重提）

| 项 | 挂起原因 | 复议触发条件 |
|---|---|---|
| **端到端送达回执**（`DELIVERED`） | 依赖外部通道能力，当前无证据支持；把"渠道接受了请求"升级为"用户已收到"在证据上不成立 | ADR-0025 D6 真实值班通道接入后，且渠道提供可验证的送达/已读回执 |
| **Alertmanager 入站投递契约** | 本文只约束出站；入站路径（`receive_alertmanager_alert`，`notification_service.py:291-328`）的幂等键、重复告警去重、持久化保证是另一主题 | 入站告警产生实际误报/重复告警处置需求时 |

### 2.4 非目标（本文不裁决）

- 重试次数、退避曲线、线程池大小、worker 数、Redis 参数、各通道 timeout 具体数值 → 实现/配置；
- 指标名、面板、阈值 → **ADR-0011**（本文只要求"投递状态必须可观测且持久化"）；
- 通知渠道管理 UI/API 形态；
- 与准入队列（ADR-0026）的有界性与背压实现细节（语义独立，但不得冲突）。

## 3. 备选方案与权衡

- **方案 A：只按三个 Issue 修 bug（不立 ADR）**
  - 优点：见效快。
  - 缺点：三个修复会各自发明"成功"的定义，#1117 的"不重发已成功通道"仍无依据（SAQ 整任务重跑），下一个渠道接入时重犯。已发生一次的先例是 ADR-0018 Phase 2 被静默改写。
- **方案 B：本文（独立投递语义契约）**
  - 优点：语义单点定义、跨渠道稳定、不随渠道数膨胀；与 ADR-0011 分工清晰（What vs How）。
  - 缺点：需要一次契约评审；短期内 #1117/#1122 的实现要等裁决（见 §5）。
- **方案 C：修订 ADR-0011 承载投递语义**
  - 驳回：ADR-0011 的 Accepted 前提是"仅锁定第一层指标基线，告警闭环后续独立 ADR"。其前提**未被推翻**，但**其范围从来不含投递语义**。把契约塞进去会让一份可观测性 ADR 变成投递权威源，主题错位，形成两个平行权威源。
- **方案 D：不写 ADR，写成 Agent Note**
  - 驳回：这是跨渠道、跨模块、决定未来所有渠道实现的长期契约，属"方向级决策"，按 `docs/notes/README.md` 分工归 ADR。

## 4. 影响面

- **正向**：新增渠道只需遵守契约，不需要重新讨论语义；"投递成功"在 API/UI/日志/指标四处口径一致；投递失败可追溯、可补偿。
- **代价**：需要一次契约评审；投递事实需要持久化模型（迁移）；三条主力路径需从线程池迁到队列（实现工作量在 #1117/#1122 内消化）。
- **与既有 ADR 的关系**：
  - ADR-0011 —— 上位：它定义"通知什么/何时触发/如何路由"，本文定义"投递行为"。ADR-0011 已补范围边界与指针（同 PR）。
  - ADR-0018 —— 复用：重试 owner 直接用其 SAQ 基础设施，不新建队列。
  - ADR-0026 —— 语义独立、不得冲突。
  - ADR-0027 —— 多实例下重试 owner 唯一性依赖其守卫。
  - ADR-0025 D6 —— 挂起项触发条件挂靠它。
- **不变量**：Redis 只承载队列与瞬时跨进程通信、不作为业务事实存储（`AGENTS.md`）——D6 即该不变量的投递域实例化。

## 5. 落地与后续动作

次序（本 ADR 起草不改代码）：

1. **本 ADR 定稿**（Proposed → Accepted）——跟踪 issue [#1166](https://github.com/DUElost/stability-test-platform/issues/1166)；
2. **ADR-0011 范围边界与指针**（同 PR 已补）；
3. **索引挂靠位**（`docs/adr/README.md` 清单行 + `docs/DOC-MAP.md` 架构 ADR 行，同 PR 已补）。起草期间 `docs/DOC-MAP.md` 曾有**他人在窗未提交**的断链行（指向不存在的 `ADR-0035-agent-host-identity.md`，`gov-surface` 唯一 BLOCK），已由该 Execution 处置并经复核确认 `check_governance_surface.py --check` 全绿（跟踪 issue [#1165](https://github.com/DUElost/stability-test-platform/issues/1165)，已关闭）；
4. **#1120**：按 D1/D9 修业务响应判定（可在契约定稿后立即进行）；
5. **#1117**：按 D4/D5/D6/D7 修异常吞没 + 投递级幂等（retry owner 依本文）；
6. **#1122**：按 D3/D4 收口 timeout 与队列边界（在 D4 定案后再动，避免再写一版会被推翻的参数）；
7. **实现分解与验收台账**：[#1167](https://github.com/DUElost/stability-test-platform/issues/1167)（D1–D9 → 代码的映射与顺序）。

**实现与契约的先后纪律**（对齐执行契约 §10）：实现不得静默重新定义本文语义；若实现发现本文不可行，先修订本文再改代码。

## 6. Verification

- 契约一致性：三条投递路径归一化后共用同一 `DeliveryResult` 语义（测试需覆盖**真实吞异常路径**，不得只 mock dispatcher——#1117 现状即此漏洞）；
- 三态分类：`ACCEPTED` / `REJECTED_PERMANENT` / `REJECTED_TRANSIENT` / `UNKNOWN` 各有覆盖用例，含"HTTP 成功但业务拒绝"（#1120）与"超时但请求可能已成功"（UNKNOWN）；
- 幂等：重试不重发已 `ACCEPTED` 通道；
- 有界与 deadline：无 timeout 的网络调用被测试或门禁拦截；
- 持久化：投递状态可查（1:N 可表达）。

## 7. Revisit

- 端到端送达回执成为真实需求（触发条件见 §2.3）；
- 出现第二个需要重试语义的 outbound 域（如 JIRA 提单），评估是复用本契约还是抽象为通用投递契约；
- ADR-0018 的 SAQ 承载假设被推翻（如改为外部 MQ），D4/D5 需重新裁决；
- 渠道数量增长导致 `DeliveryResult` 归一化成本超过收益。

## 8. 关联实现/文档

- `backend/services/notification_service.py` — 投递服务（本契约主要落点）
- `backend/core/thread_pool.py` — 现 fire-and-forget 载体
- `backend/tasks/saq_tasks.py` / `backend/tasks/saq_worker.py` — 异步队列与重试载体
- `backend/scheduler/recycler.py` — 唯一走 SAQ 的投递路径
- `backend/api/routes/notifications.py` — 同步测试路径与入站 webhook
- `backend/models/notification.py` — 投递事实层现状
- `docs/adr/ADR-0011-observability-and-alerting-evolution.md` — 上位（What）
- `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md:230` — 被静默改写的既有决策
