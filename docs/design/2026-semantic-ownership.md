# 跨域语义归属索引（Ownership Authority）

- **状态**：Draft（#2546 收口草案；方案 A + 评审 Accept-with-nits + 2026-09-18 用户三点修正）
- **日期**：2026-09-18
- **目的**：回答「这个概念/关系的定义权归谁」——**只做归属索引，不做内容宪法**
- **范围**：现行 **Accepted**（及同等生效）ADR 均可纳入索引；非仅 ADR-0033/0020
- **关联**：[#2546](https://github.com/DUElost/stability-test-platform/issues/2546)；评审 `docs/reviews/REVIEW_SEMANTIC_OWNERSHIP_*`；[`adr/README.md`](../adr/README.md)

---

## 0. 权责边界（Ownership Authority only）

| 层 | 回答什么 | 本仓落点 |
|---|---|---|
| Registry | 谁正在决定 | `execution-contract` / `ai_work.py` |
| **Semantic Ownership（本文）** | **谁拥有定义权（owner）** | 本文件表行 |
| Domain Authority | 事实/概念是什么 | 被指向的域内 ADR / design / 契约 |
| ADR | 采用什么方案 | `docs/adr/` |
| Code / Tests | 如何实现 | 源码与测试 |

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
| **X1** | 脚本「唯一权威」三处字面 | **内容** = ADR-0021 `### D4`；**运行时** = ADR-0033 `### D3`；`plan_snapshot.script_meta` = 派发时刻自 D4 冻结的**副本**，**不是**第三权威源 | 采纳多数评审；0021 关联区括注已措辞降级（见 §8） |
| **X2** | 日志域四层权威 | **四层都对**；表内分四行登记。**不是冲突**，是缺可引用汇总 | S15 **不得**把四层并存判成 ≥2 owner |
| **X3** | merge 执行位置归属 | **现状登记** = ADR-0027 清单**第 7 条**；**产物与中心布局** = ADR-0025；**B1 迁 worker 触发后**结构面才归 ADR-0033 | 与 B0 相容；边行 `R-merge-locus` |

---

## 2. Tool vs Script vs Adapter（单行定义）

| 词 | 单行定义 |
|---|---|
| **Script** | DB **script catalog** 中可被 PlanStep 以 `script:<name>` **派发**的运行时单元（版本行、`param_schema`、capabilities）；运行时权威见 ADR-0033 D3 |
| **Tool** | 相对平台产品边界而言的**可替换实现面**：原厂/大体积/客户特化资产，或以包形态版本化的外部引擎；**新族禁止全量源码拷进 `backend/agent/scripts/`**（ADR-0033 D0） |
| **Adapter** | 仓内**防腐边界**（Contract / argv·API 翻译 / 退出码映射 / 客户或厂商差异插件点），把易变面接到平台深嵌的核心能力或 Script/SAQ；见 ADR-0033 D4 |

**产品口径（用户修正 2026-09-18）**：

> **B（日志链）与 C（JIRA）的核心能力直接深嵌平台代码**（二者在建平台前即已自研，现为平台几大核心功能）。深嵌 ≠ 无边界糊进 `scripts/`：核心能力进平台可维护模块；厂商差异 / 客户特化 / 原厂二进制仍用 Adapter（或明确外置资产）隔离，以便适配与升级。  
> **A（专项）与 D（刷机）**仍是「编排/提权深嵌 + 大包/原厂外置」。

---

## 3. 四类工具：嵌入面 / 外置面（修订）

| 类 | 平台应深嵌（主路径） | 仍解耦 / 适配隔离（可维护性） | 0033 宿主 | 口径 |
|---|---|---|---|---|
| **A. 专项测试** | PlanStep、Device Lease、退出码→步骤终态、`support_files`、param 版本化、specialty 标签 | 大体积专项包、APK、独立压测引擎本体；新族 Contract+包，禁止新族全量入 `scripts/` | Tier 3 | 编排深嵌 + 本体外置（不变） |
| **B. 终端日志链** | **核心能力进平台**：DLE / `log_signal` / 分区与完备性 / merge **调度与编排逻辑** / 归档状态机 / scan-upload-merge 契约实现；去重·汇总的**平台侧引擎与数据模型**（可维护模块，非「只留接口」） | **仍隔离**：厂商 CLI/二进制差异、路径与 argv 方言、§5.4 已登记过渡例外（展锐三族裸目录）——用 Adapter/`DedupMergeEngine` 边界收口；**禁止**把又一整棵厂商树无边界复制进 `scripts/` 或扩散 §5.4 例外 | Tier 1+2 | **深嵌为主** + 适配隔离易变面 |
| **C. JIRA 自动化** | **核心能力进平台**：草稿模型、策略引擎、幂等/审计、回写 issue key、IssueTracker 产品闭环（ADR-0012 全层目标） | **仍隔离**：各客户 Jira REST/字段映射/鉴权方言（Adapter 或客户插件）；凭据不落设备端；禁止客户特化散进 `dedup_scan` / 通用 service 主干 | Tier 1 | **深嵌为主** + 客户适配边界 |
| **D. 刷机自动化** | Plan 编排、指纹路由、固件仓库契约、刷前/刷后核验、`stp-agent-priv` 提权边界 | SP_Flash_Tool 等原厂二进制、机型固件包；仓内调用胶水/Adapter | Tier 3 + 提权（本表补登记） | 编排/提权深嵌 + 原厂外置（不变） |

```text
A/D：  平台深嵌编排(+提权)     Adapter 薄边          原厂/大包外置
B/C：  平台深嵌核心能力模块     Adapter 隔离方言      仅厂商/客户易变面外置或插件化
共通红线：禁止不可维护的「全量复制进 scripts/」；D0 对新族仍生效
```

---

## 4. S15 可机读判据（表内；Accept-with-nits）

> S15 号位在 `check_governance_surface.py` 空闲。本草案钉判据形状；**门禁实现 follow-up**。

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

| 域 | 代表 Accepted ADR | 本草案状态 |
|---|---|---|
| 控制面 / 执行分层 | 0001, 0006, 0014, 0016, 0017, 0018 | 已填关键行；其余见 TBD |
| 状态机 / 租约 / 调度 | 0003, 0019, 0022, 0026, 0027, 0048 | 已填关键行 |
| Plan / 脚本 / 工具接入 | 0020, 0021, 0023, 0033, 0039→Proposed 不强制 | 已填 X1 相关 + flash |
| 日志 / 存储 / merge | 0025, 0028, 0032 + design 契约 | 已填 X2/X3 |
| 后处理 / Jira | 0012 | 已填（深嵌口径见 §3） |
| 会话 / 安全 | 0024 | 已填 |
| 主机身份 / 提权 / 退役 / 安装 | 0035, 0037, 0038, 0040, 0044 | 已填关键行 |
| 可观测 / 通知 | 0011, 0036 | 已填关键行 |
| 项目 / 套件 / AI | 0029, 0030, 0031 | **TBD 占位**（触碰即填） |
| 配置 / 词表 / 站点 | 0042, 0045, 0041 | 已填或 TBD |
| 多 Harness 执行 | 0034；细则 `execution-contract` | 已填 |
| Schema / 审计 / 前端扩展 | 0008, 0015, 0013 | **TBD 占位** |

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
| `script-meta-freeze` | concept | 派发冻结副本；非独立权威 | `docs/adr/ADR-0021-script-content-alignment-gate.md :: ## 引用 / 关联` | 再称「唯一权威」 |
| `dle-record` | concept | 设备日志事件终态台账 | `docs/adr/ADR-0028-device-log-event-and-continuous-upload.md :: 唯一权威记录` | 改唯一记录主张 |
| `log-signal-stream` | concept | 异常事件权威流 | `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md :: \`log_signal\` 是异常事件权威流` | 旁路上报 |
| `dedup-pipeline-behavior` | concept | 并列 dedup/merge 行为与分区 | `docs/adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md :: ### D1：两条并列流水线，禁止交叉混用` | 混流水线 |
| `center-storage-model` | concept | 中心存储 / 归档布局 | `docs/adr/ADR-0025-phase4-architecture-alignment.md :: ### D4: 日志归档——三阶段（搬运 + 汇总去重 + 分类提取）` | I-12 确认后修 0025 |
| `R-merge-locus` | relation | `Merge ─executed_at→ 控制面实例` | `docs/adr/ADR-0027-control-plane-horizontal-scaling.md :: 7. **merge（\`run_merge_sync\`）为实例绑定操作**` | B1/B2/多实例互斥 |
| `R-merge-consumes-log` | relation | `Merge ─consumes→ scan/日志产物` | `docs/design/2026-scan-upload-merge-contract.md :: ## 控制面 merge` | 改输入集 |
| `R-tool-hosted-by-tier` | relation | `工具实现 ─hosted_by→ Tier` | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: ### D1：确立严格的三层工具宿主分类与生命周期隔离` | 新宿主层 |
| `flash-tool` | concept | 刷机工具族（补 0033 空洞） | 本表登记；提权 `docs/adr/ADR-0037-agent-host-privilege-boundary.md :: D5 flash 链运行时提权收敛`；编排 ADR-0020 | 0033 D1 正式写入 |
| `jira-post-completion` | concept | 后处理/Jira **平台核心闭环**（深嵌） | `docs/adr/ADR-0012-post-completion-pipeline-jira-automation.md :: 第 1 层（✅ 已实现）` | 第 2–3 层；客户方言回渗主干 |
| `session-cookie-csrf` | concept | Web 会话 / CSRF / refresh 吊销 | `docs/adr/ADR-0024-browser-session-security-hardening.md :: ## 决策` | 改 Secure/SameSite 边界 |
| `host-privilege-wrapper` | concept | Agent 主机单一提权入口 | `docs/adr/ADR-0037-agent-host-privilege-boundary.md :: ## 2. 决策` | 宽 sudoers 回流 |
| `host-retirement` | concept | 主机退役终态语义 | `docs/adr/ADR-0038-host-retirement-semantics.md :: ## 2. 决策` | DELETE 与 retire 再混 |
| `notification-delivery` | concept | 通知投递成功/失败语义 | `docs/adr/ADR-0036-notification-delivery-semantics.md :: ### 2.1 投递管道（契约对象）` | 改 ACCEPTED≠DELIVERED |
| `execution-registry` | concept | 多 Harness Execution Registry / 三维状态 | `docs/adr/ADR-0034-multi-harness-execution-contract.md :: ### 2.3 状态模型：lifecycle × liveness × integration 三维正交 — 细则见契约 §3`；细则 `docs/development/ai/execution-contract.md :: ## 3. 状态模型（三维）与 transition table` | 改 Registry 为调度器 |
| `settings-bare-read` | concept | 配置读取收敛与裸读边界 | `docs/adr/ADR-0042-settings-convergence-and-bare-read-boundary.md :: ## 决策` | 新域绕过分域 settings |
| `risk-level-vocab` | concept | 风险对外词表 S/A/B | `docs/adr/ADR-0045-risk-level-vocabulary.md :: ## 2. 决策` | 多词表回流 |
| `project-taxonomy` | concept | TestProject / specialty 分类 | TBD → `docs/adr/ADR-0029-project-taxonomy-and-param-layering.md` | 触碰 0029 时补锚 |
| `multi-case-suite` | concept | test_suite / test_case 多用例 | TBD → `docs/adr/ADR-0030-multi-case-suite-management.md` | 触碰 0030 时补锚 |
| `platform-ai-assistant` | concept | 平台 AI 助手边界 | TBD → `docs/adr/ADR-0031-platform-ai-assistant.md` | 触碰 0031 时补锚 |
| `agent-host-identity` | concept | Agent 主机身份与凭据 | TBD → `docs/adr/ADR-0035-agent-host-identity.md`（现行接受边界 §3） | 升级触发 §6 |
| `schema-alembic-only` | concept | Schema 迁移唯一路径 | TBD → `docs/adr/ADR-0008-schema-migration-governance-alembic-only.md` | 旁路迁移 |
| `audit-log` | concept | 审计日志语义 | TBD → `docs/adr/ADR-0015-audit-log-system.md` | 与 Y2 词表冲突时优先填 |
| `site-delivery` | concept | 独立站点交付边界 | TBD → `docs/adr/ADR-0041-independent-site-delivery-and-management.md` | 多站复制倒逼工具入仓 |

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
| N6 | 五层模型 | §0 表 |
| S15 nits | 表内判据；不复用 S2；锚用结构化/` :: `；③字段驱动 | §4 |
| X1/X2/X3 | 评审推荐单行口径 | §1 |
| F-5 | flash 补登记 | `flash-tool` |
| 触碰即补 | `归属域` 不做一次扫全库 | §7、adr/README |

### 6.2 未采纳 / 延后（及理由）

| 项 | 理由 |
|---|---|
| 本 PR 实现 S15 代码 | 表形状与 B/C 口径刚修订；先合入索引再开门禁，避免假绿锁死 |
| `LINK_TREES += docs/adr`（e82515） | 有价值的引用面门禁，正交于 ownership 表；另开治理单 |
| 首批强行纳入 Y1/Y2 全行（c42fb9） | #2546 非目标已写明；`audit-log` 已 TBD，触碰 #2629/#2631 时填 |
| 方案 B / 全量抽名词 | 评审一致 Needs-revision；本修订是「Accepted ADR 覆盖框架 + 关键行」，不是 Inventory |
| 本 PR 改 ADR-0033 D1 正文加 flash | 本表已补登记；正文表留给 0033 Accepted 修订 |
| 合并/重编号 ADR | N5 / issue 纪律 |
| 一次补齐全部 ADR 头部 `归属域` | 字段驱动；假阳性面过大 |

### 6.3 用户相对初评的增量（本修订强制采纳）

| 修正 | 相对初评/分析稿 | 落点 |
|---|---|---|
| B/C **深嵌为主** | 纠正「引擎本体一律外置 / 只嵌调度契约」过窄说法 | §2–§3 |
| 索引覆盖**全部现行 Accepted ADR** | 纠正「像只服务 0033/0020」的窄框架 | §5.1 矩阵 + TBD 策略 |

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
| `ADR-0021` 关联区 `script_meta` 括注 | 已降级为冻结副本 / 非独立权威（本 PR） |
| ADR-0033 D1 无 flash | 本表 `flash-tool` 补登记；正文表后续修订 |

---

## 9. 非目标

- 不替代 #2108；不实现 0033 Phase 2；不改脚本版本树；不合并重编号 ADR。
- Y1/Y2 机制受益于本表，关单范围仍以触碰时补行为准。

---

## 10. 修订记录

| 日期 | 变更 |
|---|---|
| 2026-09-18 | 初稿：方案 A + Ownership only + X1/X2/X3 + 四类嵌入 + flash + S15 + 12 行 |
| 2026-09-18 | **用户三点修正**：B/C 深嵌为主；Accepted ADR 全覆盖框架 + 扩表/TBD；评审采纳对照 §6 |
