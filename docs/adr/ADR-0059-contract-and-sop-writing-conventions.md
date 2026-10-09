# ADR-0059: 契约类文档与 SOP 的写作约定（参考 ASD-STE100 原则，不采纳其标准）
- 状态：**Proposed** v0.1（2026-10-09 起草，待 Owner 裁决）
- 版本记录：v0.1 2026-10-09 起草（[#3622](https://github.com/DUElost/stability-test-platform/issues/3622)；已吸收一轮外部评审，见 §1.5）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-10-09
- 决策者：Owner（待裁决）；起草：Claude Code（云端工作面）
- 标签：文档治理, 写作约定, 术语, 规范强度, SOP, 多 Harness
- 归属域：n/a（写作约定不登记概念归属；术语对照按 D2 写进各概念的 owner 文档）

> 本文的「决策」节按自身 D3 书写：规则句只用「必须 / 不得 / 建议 / 可以」四个强度词，
> 并写出执行者。

## 1. 背景

### 1.1 起因

有人建议用 ASD-STE100（Simplified Technical English）规范 STP 文档的语义。STE 是英语受控语言：
Issue 9（2025-01 发布）含 53 条写作规则和约 900 个批准词，每个批准词只有一个意思和一个词性；
程序句最多 20 词，描述句最多 25 词；项目自有的名词与动词（technical nouns / technical verbs）
由使用方自行定义。

STE 不能原样用于本仓：

| 事实 | 依据（基线 `main@5e0b6b1`，2026-10-03） |
|---|---|
| 文档以中文为主 | 1724 篇 Markdown 中 1676 篇（97%）以中文为主；159 篇权威文档（根与目录内 `AGENTS.md`，`docs/` 下 design / development / operations / adr / prd / acceptance，11 个 skill）全部以中文为主。没有找到 STE 的中文版 |
| 句长不是主要问题 | 权威文档正文句长（汉字数 + 英文词数）中位 15、P90 为 31，超过 40 的占 3.4% |
| 文档类型不同 | STE 为维修手册的程序与描述文字设计；本仓大头是 ADR、审查报告和 1363 篇 Agent Note，属论证型文字 |

因此本 ADR 只借用 STE 的三条原则：一词一义、批准词有固定含义、程序写作与安全提示规则。
STE 的词典和句长上限不采纳。

### 1.2 真实存在的问题

**一词多义。** 「任务」在 65 篇权威文档中出现 196 次，至少指五种东西：SAQ 后台任务
（`docs/design/02-backend.md` 的 `post_completion.py` 行）、平台定时任务实体
（`docs/adr/ADR-0029-project-taxonomy-and-param-layering.md` 的「定时任务引用的 Plan」）、操作系统定时器
（`docs/development/script-versioning.md` 的「运维或定时任务」）、卡住的 PlanRun / Job
（`.claude/skills/diagnose-device-stall/SKILL.md:3` 的「任务卡死」）、交给 AI 的工作范围
（`docs/design/2026-adr-0025-log-flow-sequence.md` §4.7）。前端通知页把 `RUN_FAILED` 显示为「任务失败」
（`frontend/src/pages/notifications/NotificationsPage.tsx:51`），即界面上「任务」指 PlanRun。

**多词一义。** 「控制面」644 次、「控制平面」61 次、「control plane」58 次。
`docs/design/2026-storage-roles-and-aliases.md:7` 已规定「正文只用推荐名」且推荐名是「控制面」，
但 `docs/design/2026-log-chain-global-semantics.md:90` 仍写「控制平面」。规则存在，写作时看不到。

**混淆造成过阻塞。** ADR-0033 的文字曾把 Agent 主机汇总（B2）写成控制面 merge（B5），
造成 v1.4 阻塞（`2026-log-chain-global-semantics.md:77`、`:258`）。

**规范强度没有定义。** 159 篇权威文档中（正则计数，含部分描述性用法，只看量级）：

| 强度 | 现有写法与次数 |
|---|---|
| 要求 | 必须 507、需要 290、须 179、应 约 102、应当 6、务必 4 |
| 禁止 | 不得 262、不可 232（不含「不可变」）、不能 193、禁止 136、不要 101、不应 18、严禁 4 |
| 推荐 | 建议 102、推荐 55 |
| 允许 | 允许 126、可以 34 |

没有文档说明「不要」是否和「不得」一样硬，也没有说明「需要」是要求还是描述。
`AGENTS.md` 不到 80 行就混用「必须、禁止、不得、不要、不用、只允许」，还有陈述句形式的规则
（如「实施者不重新设计」）。AI 读者与复核者难以判断一句话是硬规则还是建议。

**SOP 的步骤结构。** `docs/operations/device-lease-emergency-release.md` 写生产业务库，
但「不得触碰 `device` 表」写在 UPDATE 之后（第 38 行）；第 14 行一句话里有两个检查和一个停止条件，
其中「确认设备没有在途 PlanRun 引用」没有给出查法。

### 1.3 已有机制（复用，不另建）

| 机制 | 管什么 | 位置 |
|---|---|---|
| Semantic Ownership | 概念的定义权归谁；只管归属，不管内容；S15 不从散文推导「同一概念」、不做「新双标必红」 | `docs/design/2026-semantic-ownership.md`（§0、§4，第 90、121 行） |
| 机器字符串规范值 + 别称 + 读写守卫 | 审计 `resource_type` 的多词一义（#2778 / #2872） | `backend/core/audit.py:41`、`backend/tests/test_audit_read_side_alias_guard.py` |
| 枚举词表漂移测试 | 状态、风险词表前后端同源 | `tests/test_status_vocabulary_drift.py`、`tests/test_risk_vocabulary_drift.py` |
| 推荐名 / 合法别称表 | 散文里存储与部署角色的多词一义 | `docs/design/2026-storage-roles-and-aliases.md` |
| 易混概念对照表 | 日志链里容易混的概念 | `docs/design/2026-log-chain-global-semantics.md` §4 |
| 同名词限定 | 两个 `lifecycle` | `docs/development/ai/execution-contract.md:15` |

缺的不是新机制，而是：散文侧没有写明规则用词的强度；SOP 没有步骤写法；
已有的推荐名在写作时看不到。

### 1.4 治理约束

- 本 ADR 改变契约类文档的写作方式，属方向级决策，必须先由 Owner 裁决。
- `AGENTS.md` 已用 79/80 行（S6 预算），不能新增规则段。
- `AGENTS.md` 的硬不变量原文由 S11 锚点（`tools/dev/check_governance_surface.py` 的
  `HARD_INVARIANT_ANCHORS`）逐字固定。
- 新增门禁须有复发数据支撑（ADR-0058 备选 C 的否决理由）。

### 1.5 外部评审（v0.1 已吸收）

2026-10-03 一轮 ChatGPT 独立评审（「术语方案评估审查」，基于同一基线 `main@5e0b6b1`；
评审者无法读取起草会话，按仓库事实给出条件式裁决）。结论与本稿的处理：

| 评审意见 | 处理 |
|---|---|
| 不建覆盖全仓的术语大词典，不扫全仓抽词，不用 CI 检查术语一致性（与 #2546 方案 B 同类） | 采纳：撤回起草初稿的中心术语表、「新概念先登记」和「按频次挑首批 20–30 条」 |
| 没有实际歧义、没有跨边界契约，就不登记 | 采纳为 D2-3 的触发条件 |
| 一词多义靠写明语义轴，不强求「一个词只有一个意思」 | 采纳为 D2-2 |
| 机器契约字符串沿用 canonical + alias + 读写守卫 | 采纳为 D2-4，本 ADR 不管 |
| 不上全库自然语言 linter | 采纳为 D5 |
| 文档散文是最弱一级 | 部分采纳：SOP 与 skill 会被 AI 直接执行，D2-2 对它们是「必须」 |
| 用 semantic-ownership 的 key 充当概念 ID | 收窄：只用于有归属争议的架构概念；日常用词不进归属索引（其每行受 S15 校验，§0 禁止解释列） |

评审未覆盖本稿的 D3（强度词）与 D4（SOP 步骤），二者与术语治理无关。

## 2. 决策

### D1 适用范围

本约定只适用于下列文档中**新写或改动的句子**：

- **契约类**：根目录与目录内的 `AGENTS.md`；`docs/development/ai/execution-contract.md` 及其附录；
  ADR 的「决策」节；`docs/design/*-contract.md`。
- **SOP 类**：`.claude/skills/*/SKILL.md`；`docs/operations/` 下的流程文档（`incident-*` 事故记录除外）；
  `docs/` 根目录下的 runbook 与 checklist。

ADR 的背景与备选节、`docs/reviews/`、`docs/notes/`、`docs/archive/`、代码注释和对话输出不适用。
作者不得为满足本约定改写同一文件中未触碰的句子（「只改当前 Requirement 必需内容」）。

### D2 约定一：术语

1. 已有推荐名的概念，作者必须只用推荐名。现有对照表见 §1.3 的后三行。
   反引号内的代码标识符不受此限。
2. 一个词在上下文中可能指多个概念时，作者必须写明指哪一个（加限定语），例如写「SAQ 任务」
   「定时任务」「systemd timer」「PlanRun」，不写单独的「任务」。SOP 类文档必须做到；
   契约类文档建议做到。
3. 只有出现实际歧义（被误读、复核发现、造成事故）或一个词跨边界使用（用作界面文案、API 或数据库值、
   跨文档契约）时，作者才可以新增对照条目。条目必须写进该概念的 owner 文档，沿用
   storage-roles 的「推荐名 | 合法别称 | 不要当成」格式。不得新建中心术语表。
4. 机器契约字符串（数据库值、API 枚举、审计类型、事件类型、指标标签）不归本约定管，
   沿用 #2778 / #2872 的规范值、别称表加读写守卫。
5. 对照表只决定用哪个词；词的含义由 owner 文档定义。对照表不得写定义。

旧文档不改写，别称因此需要长期保留（与 append-only 审计行的处理相同），对照条目不设退役流程。

### D3 约定二：规则句的强度词

| 级别 | 规则句只用 | 含义 | 规则句里不再用 |
|---|---|---|---|
| 要求 | 必须 | 不做即违规 | 须、需要、应、应当、务必 |
| 禁止 | 不得 | 做了即违规 | 禁止、严禁、不要、不用、不应 |
| 推荐 | 建议 | 不照做时须写理由 | 推荐、宜 |
| 允许 | 可以 | 做或不做都合规 | 可、允许 |

1. 规则句的结构是「执行者 +（条件）+ 强度词 + 动作」，一句只写一条规则。执行者用固定角色名，
   例如 Owner、规划者、实施者、复核者、Agent。
2. 「不能」「不可」「无法」只表示做不到，不表示禁止。「需要」只表示依赖（如「运行测试需要 PG」）。
3. 清单式规则可以在节标题或导语中统一声明强度，条目内不再重复。`AGENTS.md`「硬不变量」节
   按此处理，作者不得为本约定改写其 S11 锚定原文。
4. 选「必须」「不得」而不选 GB/T 1.1 的「应」或「禁止」：前两者在仓库中最常用（507 次、262 次），
   「不得」可以接主语。改用「应」要改约 500 处「必须」，含义不变。

### D4 约定三：SOP 步骤

1. 一步只写一个动作。只有必须同时完成的动作，才写进同一步。
2. 条件写在动作前面：「如果 X，……」。停止条件单独写成一步。
3. 警告单独成段，放在它保护的步骤之前，写明不得做什么、做了的后果。
4. 用祈使句，以动作开头。执行者不是读者本人时，写出执行者。
5. （项目补充）会改变状态的步骤后写「预期：」，给出能核对的结果。
6. （项目补充）「确认 X」类检查必须写出查法：命令、查询语句或页面。

规则 1–4 来自 STE 的程序写作与安全指令规则及维修手册惯例；规则 5–6 是本项目补充。

### D5 写作入口，不新增门禁

1. 约定正文落在 `docs/development/writing-conventions.md`，登记进 `docs/README.md` 与 `docs/DOC-MAP.md`。
2. 新建 skill `.claude/skills/contract-sop-writing/`，内容只写「读取并遵守 writing-conventions.md」，
   与 `device-lease-release` 同为转指写法。Claude Code 直接加载；Codex 与 Cursor 经 `.agents/skills`
   链接发现。不改 `docs/development/ai/harness-adapters.md`。
3. 在 `AGENTS.md`「提交前」的 Agent Note 那一行末尾追加一个链接，不增加行数。
   只读 `AGENTS.md` 的 Harness（OpenCode、Zcode、CodeBuddy CLI、dsh）由此看到约定。
4. 在 `.github/pull_request_template.md`「文档」节加一个勾选项，复核者按 D2–D4 核对。
5. 维护者不得为本约定新增 CI 门禁或散文 lint（与 S15「不从散文推导同一概念」一致）。
   复议条件见 §6。

### D6 生效与迁移

1. 写作规则在 D5 第 1–4 项合入后生效。合入前，本 ADR 不约束任何文档。
2. 任何人不得宣布「没有强度词的句子不是规则」。存量契约没有迁移时，这会让 `AGENTS.md` 中现有的
   陈述式规则被读成描述。只有存量迁移完成后，Owner 才可以另行裁决这条阅读规则。
3. 存量文档的迁移不在本 ADR 范围内。若 Owner 决定迁移，必须另立批次，按 ADR-0058 流程执行。
4. 改动 `AGENTS.md` 的 PR 必须单独提交，且同一时间只由一个工作面修改（共享元文件串行）。
   若改写 S11 锚定的句子，同一 PR 必须同步修改 `HARD_INVARIANT_ANCHORS`。

## 3. 备选方案与权衡

| 方案 | 做法 | 不选的理由 |
|---|---|---|
| A 原样采用 STE | 把文档译成英文后套用 STE | 改变 Owner 与各 Harness 的工作语言；论证型文字损失表达力；翻译成本远超收益 |
| B 自造「中文 STE」 | 中文批准词表 + 句长上限 | 句长不是问题（§1.1）；词表维护成本高；已不是 ASD-STE100 |
| C 中心术语表（起草初稿） | 新建术语表文件，新概念先登记后使用，按出现频次挑首批 20–30 条 | 每次开发都要多维护元数据；频次不等于歧义；有成为第五份内容权威的风险。经 §1.5 评审撤回 |
| D 改用 GB/T 1.1 的「应 / 宜 / 可」 | 采用国标助动词体系 | 需改约 500 处「必须」，含义不变（D3-4） |
| E 散文 lint | CI 检查禁用别称或强度词 | 无复发数据；中文散文的误报不可控；与 S15 原则冲突 |
| F 只做术语 | 不管强度词与 SOP 步骤 | 强度歧义与 SOP 结构问题有独立证据（§1.2），术语治理解决不了 |
| G 不做 | 维持现状 | 已有推荐名在漂移；强度歧义直接影响 AI 读者判断规则 |
| **H 本方案** | 三条约定 + 写作入口，限定范围，只约束新写与改动的句子，不加门禁 | —— |

## 4. 影响

**收益**
- 复核者与 AI 读者能从用词判断一句话是硬规则、建议还是描述。
- SOP 的危险步骤前有警告，检查步骤有查法；试改租约释放流程即暴露了缺失的 PlanRun 查法。
- 已有的推荐名在写作时可见，不再只靠记忆。
- 不新建中心术语表，不新增门禁，不改 semantic-ownership 表与审计词表。

**代价**
- 作者写契约与 SOP 时多一次自查；复核者多一个勾选项。
- 新旧写法会长期并存，直到 Owner 另行决定是否迁移存量。
- 约定的执行依赖复核，没有机器保证。

## 5. 落地与后续动作

以下动作只在 Owner 接受本 ADR 后执行，每项单独成 PR：

| 单元 | 内容 | 说明 |
|---|---|---|
| U1 | `writing-conventions.md`、skill、PR 模板勾选项、`docs/README.md` 与 DOC-MAP 登记；在 `docs/design/05-data-model.md` 增加「任务 / 定时任务」对照条目 | 对照条目按 D2-3 触发：界面文案（`NotificationsPage.tsx:51`）与五种文档用法是现成证据 |
| U2 | `AGENTS.md`「提交前」追加链接 | 共享元文件，单独 PR，跑 `check:quick`（含 S6 / S11） |
| U3 | 试改 2–3 篇 SOP：`device-lease-emergency-release.md` 与对应 skill、`diagnose-device-stall` | 缺失的查法由该领域 owner 补，实施者不得代编 |

每个 PR 合入前，实施者必须运行 `check:quick`（其中 `gov-surface` 覆盖 S2 链接、S6 预算、
S7 skill 头部、S11 锚点、S12 ADR 索引、S15 归属域）。

## 6. 复议触发

- 同一别称在适用范围的新文字里被复核发现 3 次以上（三次法则）：复议是否对已登记的推荐名
  加字面量检查（仅限适用范围，不做语义推断）。
- U3 试点后，复核者认为强度词表无助于判断规则：复议 D3。
- 存量契约迁移完成：复议 D6-2 的阅读规则。
- 出现英文对外文档需求：重新评估 STE 原样适用于该类文档。

## 7. 关联实现/文档

- 已有机制：[`2026-semantic-ownership.md`](../design/2026-semantic-ownership.md)、
  [`2026-storage-roles-and-aliases.md`](../design/2026-storage-roles-and-aliases.md)、
  [`2026-log-chain-global-semantics.md`](../design/2026-log-chain-global-semantics.md) §4、
  [`execution-contract.md`](../development/ai/execution-contract.md)、`backend/core/audit.py`
- 流程：[ADR-0058](./ADR-0058-planned-batch-execution.md)（批次与共享元文件串行）、
  [`repository-workflow.md`](../development/repository-workflow.md)
- 试点对象：[`device-lease-emergency-release.md`](../operations/device-lease-emergency-release.md)
- 外部参考：ASD-STE100 Issue 9（ASD，2025-01）；RFC 2119 / RFC 8174；GB/T 1.1—2020 助动词体系
