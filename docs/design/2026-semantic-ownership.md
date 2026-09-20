# 跨域语义归属索引（Ownership Authority）

- **状态**：Living（#2546 Closed 后升格；Ownership Authority + S15 已在 `main` 生效；非内容宪法）
- **日期**：2026-09-20
- **目的**：回答「这个概念/关系的定义权归谁」——**只做归属索引，不做内容宪法**
- **范围**：现行 **Accepted**（及同等生效）ADR 均可纳入索引；非仅 ADR-0033/0020
- **关联**：[#2546](https://github.com/DUElost/stability-test-platform/issues/2546)（Closed）；评审 `docs/reviews/REVIEW_SEMANTIC_OWNERSHIP_*`；[`adr/README.md`](../adr/README.md)

---

## 0. 权责边界（Ownership Authority only）

| 层 | 回答什么 | 本仓落点 |
|---|---|---|
| Registry | 谁正在决定 | `execution-contract` / `ai_work.py` |
| **Semantic Ownership（本文）** | **谁拥有定义权（owner）** | 本文件表行 |
| Domain Authority | 事实/概念是什么 | 被指向的域内 ADR / design / 契约 |
| ADR | 采用什么方案 | `docs/adr/` |
| Code / Tests | 如何实现 | 源码与测试 |

> **Domain Authority 注记（N6 / F-07）**：仓内「冲突时以本文为准」的最强形态见 [`execution-contract.md`](../development/ai/execution-contract.md)（**操作规格**，可回溯修订 ADR）。其资格来自「该文即操作规格」，**不可**被本索引援引为自我授权先例——索引不是操作规格，不得抄条款、只可抄「操作规格才配挂该条款」这一资格判据。

**硬边界**：

1. 本文对**归属**拥有索引裁决权（表内一行一 owner；归属争议走 owner 人工裁决后**只改本文一行**）。
2. 本文对**内容**零裁决权：不解释「Log 是什么」「Merge 怎么跑」。
3. **禁止**书写「冲突时以本文为准」。跨文档/实现冲突时，仍以**代码与测试**为准，并以被指向的**域内文档**为内容权威（与 [`DOC-MAP.md`](../DOC-MAP.md) 一致）。
4. 本文**不得**新增细节解释列；细则一律链接到 owner 文档的结构化锚。

四分法（互不代表）：DOC-MAP = 目录粒度权威/归档；**本文 = 概念/关系粒度 owner 索引**；`AGENTS.md` 硬不变量 = 行为红线；`execution-contract` = 执行语义。

---

## 1. X1 / X2 / X3 最终口径（各一行）

| ID | 主题 | 选定口径 | 说明 |
|---|---|---|---|
| **X1** | 脚本「唯一权威」三处字面 | **内容** = ADR-0021 `### D4`；**运行时** = ADR-0033 `### D3`；`plan_snapshot` 步骤身份（`(script_name, script_version)` 等）= 派发时刻自 D4 冻结的**副本面**，**不是**第三权威源（快照**无** `script_meta` 键；#2856 / Mode C） | 采纳多数评审；0021 关联区括注已措辞降级（见 §8）；假键名勘误随 #2856 |
| **X2** | 日志域四层权威 | **四层都对**；表内分四行登记 + 路由行 `log-chain-map`。**不是冲突**，是缺可引用汇总 → [`2026-log-chain-global-semantics.md`](./2026-log-chain-global-semantics.md)。全链**地图/路由**见 [`2026-device-log-chain-contract.md`](./2026-device-log-chain-contract.md)（Living Contract，Ownership Authority only，**非**第五内容权威） | S15 **不得**把四层并存判成 ≥2 owner；`log-chain-map` **不得**当第五内容 owner |
| **X3** | merge 执行位置归属 | **现状登记** = ADR-0027 清单**第 7 条**；**产物与中心布局** = ADR-0025；**B1 迁 worker 触发后**结构面才归 ADR-0033 | 与 B0 相容；边行 `R-merge-locus` |

---

## 2. Tool vs Script vs Adapter（单行定义）

| 词 | 单行定义 |
|---|---|
| **Script** | DB **script catalog** 中可被 PlanStep 以 `script:<name>` **派发**的运行时单元（版本行、`param_schema`、capabilities）；运行时权威见 ADR-0033 D3 |
| **Tool** | **不入项目代码树**的外部工具/脚本/二进制/包（中心存储、站外发行物等）；见 ADR-0033 D0 |
| **Adapter** | 外置资产入平台后的**薄调用接缝**（argv/API 翻译、路径、退出码映射等）——**不是**并列的「可维护性架构」目标，只是接上外置物的必要胶水；见 ADR-0033 D4 |

**主判据（用户澄清 2026-09-18）**：

> 先确认**边界与落点**：外部工具与脚本**不进入项目代码本身**（不入 Git 业务树 / 不新族全量进 `scripts/`）。  
> Adapter 只回答「外置之后平台如何薄薄调用」，**不要**写成第二套哲学目标。

**产品口径（B/C 深嵌 + A/D 外置）**：

> **B / C**：深嵌的是**平台自研核心能力模块**（建平台前已自研、现为平台核心功能）——不是把厂商 CLI / 客户提交器源码拷进仓。  
> **A / D**：编排（+提权）在平台；原厂二进制 / 大包 **不入仓**。

---

## 3. 四类工具：入仓 vs 外置（修订）

**列含义**：左列 = 允许/应当进入平台代码树的能力；右列 = **外置、不入仓**的资产（括号内仅为调用接缝提示，非第二目标）。

| 类 | 平台应深嵌（入仓的自研/产品能力） | 外置 / 不入仓（含调用接缝） | 0033 宿主 | 口径 |
|---|---|---|---|---|
| **A. 专项测试** | PlanStep、Device Lease、退出码→步骤终态、`support_files`、param 版本化、specialty | 大体积专项包、APK、独立压测引擎本体（仓内仅薄调用 / Contract） | Tier 3 | 编排入仓 + 本体外置 |
| **B. 终端日志链** | **自研核心入仓**：DLE / `log_signal` / 分区与完备性 / merge 调度与编排 / 归档状态机 / 契约实现；去重·汇总的**平台侧**引擎与数据模型 | **不入仓**：厂商 scan/汇总 CLI 与二进制；§5.4 过渡例外目录不得当终态扩散。仓内可有薄 Adapter/`DedupMergeEngine` 接缝 | Tier 1+2 | 自研核心入仓；外部工具不入仓 |
| **C. JIRA 自动化** | **自研核心入仓**：草稿、策略、幂等/审计、回写 issue key、IssueTracker 闭环（ADR-0012） | **不入仓**：各客户 Jira 提交器/特化脚本本体。仓内可有薄接缝做字段映射；凭据不落设备端 | Tier 1 | 自研核心入仓；客户工具不入仓 |
| **D. 刷机自动化** | Plan 编排、指纹路由、固件仓库契约、刷前/刷后核验、`stp-agent-priv` 提权边界 | **不入仓**：SP_Flash_Tool 等原厂二进制、机型固件包（仓内仅调用接缝） | Tier 3 + 提权 | 编排/提权入仓 + 原厂外置 |

```text
主判据：外部工具/脚本 → 不入项目代码树
入仓：   平台自研核心（B/C）或编排/提权（A/D）
接缝：   Adapter = 调用外置资产的薄胶水（非并列架构目标）
红线：   D0 — 新族禁止全量复制进 scripts/
```

---

## 4. S15 可机读判据（表内；Accept-with-nits）

> S15 号位在 `check_governance_surface.py`：**已落地**（`check_ownership_table` / `check_ownership_domain_fields`，`--self-test` 含红绿样例）。文件缺失时跳过（便于叠合入）。

### 4.1 作用域（N3）

只校验**本表已登记且非 `TBD` 占位**的行。禁止从 ADR 散文推导「同一概念」。分层权威（X2）不得因字面「权威」并存而判红。

### 4.2 三条判据

| # | 判据 | 机读方式 | 分期 |
|---|---|---|---|
| **①** | 同一 `概念/关系` key **恰好一行** | 解析本文件表，按 key 唯一 | 表落地即绿 |
| **②** | 非 TBD 行的 `owner_anchor` **可解析且命中恰 1** | S15 自带解析器：`path` 存在 + 结构化定位；命中 0 或 >1 → 红。**禁止**复用 S2 验 `path:line` | 表落地即绿 |
| **③** | ADR 版本 bump 且头部**已写** `归属域：` → 本文件须同 PR 出现 | 字段驱动；未写字段不报错 | 模板字段落地后生效 |

### 4.3 锚书写格式

```text
<path> :: <结构化定位>
```

行号不进判据。引用他文可标行级豁免（理由必填）。

### 4.4 明确不做

- 不做锚点语义一致性证明（Referential ≠ Semantic Integrity）。
- 不做「新双标必红」；新双标 → 加表行 + 逐对收口。

---

## 5. 生效 ADR 覆盖框架（全 ADR 索引，非两篇工具 ADR）

索引**目标集合** = `docs/adr/README.md` 中现行 **Accepted**（含 `Accepted v*` / `**Accepted**`）条目。Proposed / Superseded / Deprecated **不强制**入表；触碰升 Accepted 时按 §7 增补。

### 5.1 域覆盖矩阵（首批）

| 域 | 代表 Accepted ADR | 本表覆盖状态 |
|---|---|---|
| 控制面 / 执行分层 | 0001, 0006, 0014, 0016, 0017, 0018 | 已填关键行；其余见 TBD |
| 状态机 / 租约 / 调度 | 0003, 0019, 0022, 0026, 0027, 0048 | 已填关键行 |
| Plan / 脚本 / 工具接入 | 0020, 0021, 0023, 0033, 0039→Proposed 不强制 | 已填 X1 相关 + flash |
| 日志 / 存储 / merge | 0025, 0028, 0032 + design 契约；全链地图 [`2026-device-log-chain-contract.md`](./2026-device-log-chain-contract.md) | 已填 X2/X3；Contract = 路由入口 |
| 后处理 / Jira | 0012 | 已填（深嵌口径见 §3） |
| 会话 / 安全 | 0024 | 已填 |
| 主机身份 / 提权 / 退役 / 安装 | 0035, 0037, 0038, 0040, 0044 | 已填关键行 |
| 可观测 / 通知 | 0011, 0036 | 已填关键行 |
| 项目 / 套件 / AI | 0029, 0030, 0031 | 已填 |
| 配置 / 词表 / 站点 | 0042, 0045, 0041 | 已填 |
| 多 Harness 执行 | 0034；细则 `execution-contract` | 已填 |
| Schema / 审计 / 前端扩展 | 0008, 0015, 0013 | 0008/0015 已填；0013 见 TBD 触发 |

> **不是**全量名词 Inventory：只登记「会影响架构决策或已出现双标风险」的概念/关系。其余 Accepted ADR 以矩阵 **TBD** 占位，增补纪律见 §7。

### 5.2 Ownership 表（预填 + 跨域扩展）

列约定：`key` 唯一；`kind` = `concept` | `relation`；`owner_anchor` 遵守 §4.3；`TBD` = 待触碰补锚。

| key | kind | 一句话 | owner_anchor | 复议触发器 |
|---|---|---|---|---|
| `control-plane-split` | concept | 控制面 vs Agent 执行面分层 | `docs/adr/ADR-0001-control-plane-and-agent-architecture.md :: ## 决策` | 合并两面或改职责边界 |
| `pipeline-action-model` | concept | Pipeline / `script:` action 唯一执行模型 | `docs/adr/ADR-0014-pipeline-execution-engine.md :: ### 执行模型` | 恢复 `shell:` 等旁路 |
| `device-lease` | concept | Device Lease / fencing / 容量 | `docs/adr/ADR-0019-android-device-lease-and-capacity-scheduling.md :: ### 1. Device Lease 模型` | 改租约粒度或锁模型 |
| `plan-run-scaling` | concept | PlanRun 准入队列与四层调度不变量 | `docs/adr/ADR-0026-plan-execution-scaling.md :: ### 2. 四条不可破坏的不变量` | 破坏 QUEUED/permit 不变量 |
| `run-terminal-semantics` | concept | 执行终态语义（完成即绿等） | `docs/adr/ADR-0048-execution-status-semantics-v2.md :: ### D1 终态语义：完成即绿，abort 才红（owner 确认）` | 恢复通过率轴 |
| `script-content` | concept | 脚本内容 / sha 对账权威 | `docs/adr/ADR-0021-script-content-alignment-gate.md :: ### D4 — 平台 DB 是脚本内容唯一权威` | 改 D4 |
| `script-runtime-catalog` | concept | 可派发 `(name, version)` 运行时权威 | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: ### D3：代码仓与工具资产包物理解耦（Manifest + Package Store）` | 第二套版本体系 |
| `script-meta-freeze` | concept | `plan_snapshot` 步骤身份冻结面；非独立权威（**无** `script_meta` 键；inventory key 名保留） | `docs/adr/ADR-0021-script-content-alignment-gate.md :: ## 引用 / 关联` | 再称「唯一权威」或把假键写成第三权威 |
| `dle-record` | concept | 设备日志事件终态台账 | `docs/adr/ADR-0028-device-log-event-and-continuous-upload.md :: 唯一权威记录` | 改唯一记录主张 |
| `log-signal-stream` | concept | 异常事件权威流 | `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md :: \`log_signal\` 是异常事件权威流` | 旁路上报 |
| `dedup-pipeline-behavior` | concept | 并列 dedup/merge 行为与分区 | `docs/adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md :: ### D1：两条并列流水线，禁止交叉混用` | 混流水线 |
| `center-storage-model` | concept | 中心存储 / 归档布局 | `docs/adr/ADR-0025-phase4-architecture-alignment.md :: ### D4: 日志归档——三阶段（搬运 + 汇总去重 + 分类提取）` | I-12 确认后修 0025 |
| `log-chain-map` | concept | 日志域全链**地图/路由入口**（Ownership Authority only；**非**第五内容权威） | `docs/design/2026-device-log-chain-contract.md :: ## 3. 阶段 owner 表（四层 + 后半段）` | 把 Contract 升格为内容宪法；与四层 ADR 抢定义权 |
| `R-merge-locus` | relation | `Merge ─executed_at→ 控制面实例` | `docs/adr/ADR-0027-control-plane-horizontal-scaling.md :: 7. **merge（\`run_merge_sync\`）为实例绑定操作**` | B1/B2/多实例互斥 |
| `R-merge-consumes-log` | relation | `Merge ─consumes→ scan/日志产物` | `docs/design/2026-scan-upload-merge-contract.md :: ## 控制面 merge` | 改输入集 |
| `R-tool-hosted-by-tier` | relation | `工具实现 ─hosted_by→ Tier` | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: ### D1：确立严格的三层工具宿主分类与生命周期隔离` | 新宿主层 |
| `flash-tool` | concept | 刷机工具族（0033 D1 已补登记） | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: 刷机补登记（v1.3 / #2546）`；提权 `docs/adr/ADR-0037-agent-host-privilege-boundary.md :: D5 flash 链运行时提权收敛` | 新刷机二进制入仓；再改 Tier |
| `jira-post-completion` | concept | 后处理/Jira **平台核心闭环**（深嵌） | `docs/adr/ADR-0012-post-completion-pipeline-jira-automation.md :: 第 1 层（✅ 已实现）` | 第 2–3 层；客户方言回渗主干 |
| `session-cookie-csrf` | concept | Web 会话 / CSRF / refresh 吊销 | `docs/adr/ADR-0024-browser-session-security-hardening.md :: ## 决策` | 改 Secure/SameSite 边界 |
| `host-privilege-wrapper` | concept | Agent 主机单一提权入口 | `docs/adr/ADR-0037-agent-host-privilege-boundary.md :: ## 2. 决策` | 宽 sudoers 回流 |
| `host-retirement` | concept | 主机退役终态语义 | `docs/adr/ADR-0038-host-retirement-semantics.md :: ## 2. 决策` | DELETE 与 retire 再混 |
| `notification-delivery` | concept | 通知投递成功/失败语义 | `docs/adr/ADR-0036-notification-delivery-semantics.md :: ### 2.1 投递管道（契约对象）` | 改 ACCEPTED≠DELIVERED |
| `execution-registry` | concept | 多 Harness Execution Registry / 三维状态 | `docs/adr/ADR-0034-multi-harness-execution-contract.md :: ### 2.3 状态模型：lifecycle × liveness × integration 三维正交 — 细则见契约 §3`；细则 `docs/development/ai/execution-contract.md :: ## 3. 状态模型（三维）与 transition table` | 改 Registry 为调度器 |
| `settings-bare-read` | concept | 配置读取收敛与裸读边界 | `docs/adr/ADR-0042-settings-convergence-and-bare-read-boundary.md :: ## 决策` | 新域绕过分域 settings |
| `risk-level-vocab` | concept | 风险对外词表 S/A/B | `docs/adr/ADR-0045-risk-level-vocabulary.md :: ## 2. 决策` | 多词表回流 |
| `project-taxonomy` | concept | TestProject / specialty 分类 | `docs/adr/ADR-0029-project-taxonomy-and-param-layering.md :: ### D2：项目实体 \`test_project\` — 单层身份 + 正交 facet` | 改 facet / 派生归属 |
| `multi-case-suite` | concept | test_suite / test_case 多用例 | `docs/adr/ADR-0030-multi-case-suite-management.md :: ### D1：用例集/用例建模为配置层实体，不进调度模型` | 用例进调度模型 |
| `platform-ai-assistant` | concept | 平台 AI 助手自治边界 | `docs/adr/ADR-0031-platform-ai-assistant.md :: ### D1：自治边界 = 运维风险四级（T0–T3）` | 越级自动执行 |
| `agent-host-identity` | concept | Agent 主机身份与凭据（现行接受边界） | `docs/adr/ADR-0035-agent-host-identity.md :: ## 3. 当前状态决策（立即生效）` | §6 升级触发 |
| `schema-alembic-only` | concept | Schema 迁移唯一路径 | `docs/adr/ADR-0008-schema-migration-governance-alembic-only.md :: ## 决策` | 旁路迁移 |
| `audit-log` | concept | 审计日志数据模型 | `docs/adr/ADR-0015-audit-log-system.md :: ### 数据模型` | 与 Y2 词表冲突时复核 |
| `site-delivery` | concept | 独立站点交付与隔离边界 | `docs/adr/ADR-0041-independent-site-delivery-and-management.md :: ### D1. 以独立站点作为运行与故障隔离边界` | 多站倒逼工具入仓 |
| `frontend-feature-expansion` | concept | 前端功能模块扩展（任务实例/问题/环境） | TBD → `docs/adr/ADR-0013-frontend-feature-expansion.md`（触碰前端 IA/模块边界时补锚；本轮不扩前端 Inventory） | 触碰 0013 或前端 IA 大改 |

---

## 6. 评审采纳对照（#2546 多 harness）

依据：`docs/reviews/REVIEW_SEMANTIC_OWNERSHIP_*`（含 004377、e82515、ae8cd9、c42fb9、54cd71、497952 等）及 issue 评论 N1–N6。

### 6.1 已采纳

| 来源 | 结论 | 落点 |
|---|---|---|
| 多稿共识 | 方案 A Accept-with-nits；拒 B 全量 Inventory；C 保留为运行期路径 | 本文定位 |
| N1 / A-01 | Ownership Authority only；禁止「以本文为准」 | §0 |
| N2 | 表含关系边 | `R-merge-*` / `R-tool-hosted-by-tier` |
| N3 | S15 仅 monitored 表行 | §4.1 |
| N4 | Referential ≠ Semantic | §4.4 |
| N5 | ADR identity 不可变；仅 Superseded/Scoped/Historical | §7 |
| N6 | 五层模型 + Domain Authority「操作规格才配挂冲突条款」注记（F-07） | §0 表与注记 |
| S15 nits | 表内判据；不复用 S2；锚用结构化/` :: `；③字段驱动 | §4 |
| X1/X2/X3 | 评审推荐单行口径 | §1 |
| F-5 | flash 补登记 | `flash-tool` |
| 触碰即补 | `归属域` 不做一次扫全库 | §7、adr/README |

### 6.2 未采纳 / 延后（及理由）

| 项 | 理由 |
|---|---|
| 本 PR 实现 S15 代码 | **已落地**（#2764） |
| `LINK_TREES += docs/adr`（e82515） | **已落地**（本轮）：修 ADR-0044/0045 幽灵链后纳入 |
| 首批强行纳入 Y1/Y2 全行（c42fb9） | #2546 非目标；`audit-log` 已有实锚，Y2 冲突时复核 |
| 方案 B / 全量抽名词 | 评审一致 Needs-revision |
| 本 PR 改 ADR-0033 D1 正文加 flash | **已落地**（#2764 / v1.3） |
| 合并/重编号 ADR | N5 / issue 纪律 |
| 一次补齐全部 ADR 头部 `归属域` | 字段驱动；假阳性面过大 |
| 前端 ADR-0013 实锚 | 本轮仍 TBD（不扩前端 Inventory） |

### 6.3 用户相对初评的增量（本修订强制采纳）

| 修正 | 相对初评/分析稿 | 落点 |
|---|---|---|
| B/C **深嵌为主** | 纠正「引擎本体一律外置 / 只嵌调度契约」过窄说法 | §2–§3 |
| 索引覆盖**全部现行 Accepted ADR** | 纠正「像只服务 0033/0020」的窄框架 | §5.1 矩阵 + TBD 策略 |
| **外置 = 不入仓**（非「适配隔离哲学」） | 澄清右列主义是资产落点；Adapter 仅为调用接缝 | §2–§3 列名与主判据 |

---

## 7. ADR 头部字段与增补纪律

- 模板：`- 归属域：semantic-ownership <key>`（见 `docs/adr/README.md`）。
- **增补触发**（满足其一即补行或把 TBD 升实锚）：
  1. 新建 Accepted ADR；
  2. 既有 Accepted ADR 版本 bump 且触及跨域概念；
  3. 出现新的字面「唯一权威」双标或边无主；
  4. 矩阵中 TBD 域被实现 PR 触碰。
- **不做**一次性全库补齐；S15③仅对已写字段生效。
- ADR identity：失去主导权 → `Superseded` / `Scoped` / `Historical` only。

---

## 8. 域内措辞降级（nits；非决策改写）

| 位置 | 动作 |
|---|---|
| `ADR-0021` 关联区 `script_meta` 括注 | 已降级为冻结副本 / 非独立权威；假键名由 #2856 去掉（本索引 X1 / `script-meta-freeze` 同步） |
| ADR-0033 D1 无 flash | 本表 `flash-tool` 已指向 v1.3「刷机补登记」句；D1 Tier3 表已列 `flash_*` |

---

## 9. 非目标

- 不替代 #2108；不改脚本版本树；不合并重编号 ADR。
- 0033 Phase 2 控制面 B5 样板（选项 A）已由他单落地（ADR-0033 v1.5）；本索引**不**承担包存储 / Phase 3 / 设备端样板。
- Y1/Y2 机制受益于本表，关单范围仍以触碰时补行为准。
- `frontend-feature-expansion` 仍 TBD（触碰 ADR-0013 / 前端 IA 大改时再升实锚）。

---

## 10. 修订记录

| 日期 | 变更 |
|---|---|
| 2026-09-18 | 初稿：方案 A + Ownership only + X1/X2/X3 + 四类嵌入 + flash + S15 + 12 行 |
| 2026-09-18 | **用户三点修正**：B/C 深嵌为主；Accepted ADR 全覆盖框架 + 扩表/TBD；评审采纳对照 §6 |
| 2026-09-18 | **外置措辞澄清**：主判据=不入项目代码树；右列改名「外置/不入仓」；Adapter=薄调用接缝非第二目标 |
| 2026-09-18 | follow-up：S15 门禁落地；ADR-0033 v1.3 D1 flash 补登记；`flash-tool` 锚改指 0033 |
| 2026-09-18 | TBD 七行升实锚；保留 `frontend-feature-expansion` TBD；`LINK_TREES += docs/adr` + 修 0044/0045 幽灵链 |
| 2026-09-19 | 增 `log-chain-map`：Device Log Chain Contract = 全链地图/路由入口（非第五内容权威）；X2 口径同步 |
| 2026-09-20 | #2546 Closed 后：**Draft → Living**；补 N6/F-07 Domain Authority 注记；§9 纠 Phase 2 过时表述（选项 A 已他单落地） |
| 2026-09-20 | X1 / `script-meta-freeze`：去掉幽灵 `plan_snapshot.script_meta` 假键名，对齐 #2856 / Mode C（步骤身份冻结面） |
