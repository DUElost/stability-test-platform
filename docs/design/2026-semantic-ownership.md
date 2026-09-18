# 跨域语义归属索引（Ownership Authority）

- **状态**：Draft（#2546 收口草案；方案 A + 评审 Accept-with-nits）
- **日期**：2026-09-18
- **目的**：回答「这个概念/关系的定义权归谁」——**只做归属索引，不做内容宪法**
- **关联**：[#2546](https://github.com/DUElost/stability-test-platform/issues/2546)；评审 `docs/reviews/REVIEW_SEMANTIC_OWNERSHIP_*`；工具口径草案见 Project store `tool-ownership-and-structure.md`

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
| **X1** | 脚本「唯一权威」三处字面 | **内容** = ADR-0021 `### D4`；**运行时** = ADR-0033 `### D3`；`plan_snapshot.script_meta` = 派发时刻自 D4 冻结的**副本**，**不是**第三权威源 | 采纳多数评审；0021 关联区对 0020 的「作为权威」括注按 nits **措辞降级**（见 §7），不改 0020/0021/0033 决策方向 |
| **X2** | 日志域四层权威 | **四层都对**；表内分四行登记（记录 / 异常流 / 行为与分区 / 存储模型）。**不是冲突**，是缺可引用汇总 | 与 #530 分层自洽同形；S15 **不得**把四层并存判成 ≥2 owner |
| **X3** | merge 执行位置归属 | **现状登记** = ADR-0027 多实例清单**第 7 条**（实例绑定 / flock）；**产物与中心布局** = ADR-0025；**B1 迁 worker 触发后**结构面才归 ADR-0033 | 与 B0「不提前 B」相容；边行见 §5 `R-merge-locus` |

分歧处选定：X1 第三腿按「引述过强 → 降级为冻结副本」处理（非另立权威）；X3 不以 0025 或 0033 独占「执行位置」全文。

---

## 2. Tool vs Script vs Adapter（单行定义）

| 词 | 单行定义 |
|---|---|
| **Script** | DB **script catalog** 中可被 PlanStep 以 `script:<name>` **派发**的运行时单元（含版本行、`param_schema`、capabilities）；权威见 ADR-0033 D3 |
| **Tool** | 平台外的工具**本体**（厂商 CLI / 二进制 / APK / 客户提交器等），以包或中心 `tools/{name}/` 过渡形态存在；**不得**再把新族全量源码拷进 `backend/agent/scripts/`（ADR-0033 D0） |
| **Adapter** | 仓内**防腐薄层**（Contract / argv 翻译 / 退出码映射 / 路径契约），把 Tool 接到 Script 或 SAQ 任务；见 ADR-0033 D4 |

一句话：平台深嵌调度面 + 契约面 + 事实面；工具本体外置；仓内只留 Adapter / Manifest / 极薄胶水。

---

## 3. 四类工具：嵌入面 / 外置面

| 类 | 平台应嵌入（深嵌） | 应外置（解耦） | 0033 宿主 |
|---|---|---|---|
| **A. 专项测试**（Monkey / 开关机 / 休眠 / GPU / MTBF…） | PlanStep、Device Lease、退出码→步骤终态、`support_files`、param 版本化 | 大体积专项包、APK、独立压测引擎本体；新族走 Contract+包 | Tier 3 |
| **B. 终端日志链**（采集 / 导出 / 去重 / 汇总） | DLE / `log_signal` / 分区路径 / merge **调度** / 归档契约；`DedupMergeEngine` **接口** | Start-Log-Scan、Scan-Result-GT、厂商 scan 二进制；§5.4 过渡例外**不得扩散** | Tier 1+2 |
| **C. JIRA 自动化** | 草稿缓存、策略（是否提单）、幂等/审计/回写 issue key（ADR-0012） | 各客户 Jira 提交 CLI/脚本 | Tier 1 |
| **D. 刷机自动化** | Plan 编排、指纹路由、固件仓库契约、刷前/刷后核验、`stp-agent-priv` 提权边界（ADR-0037） | SP_Flash_Tool 等原厂二进制、机型固件包 | Tier 3 + 主机提权（**原 0033 分类空洞，本表补登记**） |

---

## 4. S15 可机读判据（表内；Accept-with-nits）

> S15 号位在 `check_governance_surface.py` 空闲。本草案钉判据形状；**门禁实现可同 PR 或紧随 follow-up**，但合入前不得宣称「散文语义一致已机读」。

### 4.1 作用域（N3）

只校验**本表已登记行**（monitored whitelist）。禁止从 ADR 散文推导「同一概念」。分层权威（X2）不得因字面「权威」并存而判红。

### 4.2 三条判据

| # | 判据 | 机读方式 | 分期 |
|---|---|---|---|
| **①** | 同一 `概念/关系` key **恰好一行**；不得出现两行不同 owner | 解析本文件 ownership 表，按 key 唯一 | 表落地即绿 |
| **②** | 每行 `owner_anchor` **可解析且命中恰 1** | S15 **自带**解析器：`path` 存在 + **结构化定位**（节标题 `### …` / 表行 key / 或 S11 式原文子串模式）；命中 0 或 >1 → 红。**禁止**「复用 S2」验 `path:line`（S2 只验 markdown 相对链接文件存在，跳过锚与代码块） | 表落地即绿 |
| **③** | ADR 版本 bump 且头部**已写** `归属域：` → 本文件须出现在同一 PR | 字段驱动：未写 `归属域` 的 ADR **不报错**（避免一次扫全库全红） | 模板字段落地后生效 |

### 4.3 锚书写格式（硬约束）

```text
<path> :: <结构化定位>
```

- 分隔符用 ` :: `（空格+双冒号+空格），避免 `#` 与 Markdown 节标题 `###` 粘连成假锚。
- 结构化定位优先：完整节标题（含 `###`）、清单条款原文子串、或表格行 key。
- 行号仅作人读跳转提示，**不进** S15 判据（弱行号 = 假绿，同 S5x）。
- 引用他文 / 备选区原文可标行级豁免（理由必填），学 S14 #2249。

### 4.4 明确不做

- 不做「锚点语义仍与当前规则一致」（Referential ≠ Semantic Integrity）。
- 不做「出现新双标时 S15 必红」（「新双标」不可机读）；新双标走表登记 + 逐对收口（方案 C 保留为运行期路径）。

---

## 5. Ownership 表（预填；8–12 行）

列约定：`key` 唯一；`kind` = `concept` | `relation`；`owner_anchor` 遵守 §4.3。

| key | kind | 一句话 | owner_anchor | 代码/派生面（提示，非权威） | 复议触发器 |
|---|---|---|---|---|---|
| `script-content` | concept | 脚本磁盘字节 / sha 对账的内容权威 | `docs/adr/ADR-0021-script-content-alignment-gate.md :: ### D4 — 平台 DB 是脚本内容唯一权威` | `plan_snapshot.script_meta[*].content_sha256`；对齐门禁 | 改 D4 判定或对账字段 |
| `script-runtime-catalog` | concept | 哪个 `(name, version)` 可被派发的运行时权威 | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: ### D3：代码仓与工具资产包物理解耦（Manifest + Package Store）` | DB script catalog；`tool_manifest.yaml` 仅为发布格式 | 改 D3 或引入第二套版本体系 |
| `script-meta-freeze` | concept | 派发冻结副本（追溯/幂等）；**非独立权威** | `docs/adr/ADR-0021-script-content-alignment-gate.md :: ## 引用 / 关联`（措辞已降级，见 §7） | `plan_snapshot.script_meta`；ADR-0020 快照隔离 | 若再次被写成「唯一权威」 |
| `dle-record` | concept | 设备日志事件终态台账 | `docs/adr/ADR-0028-device-log-event-and-continuous-upload.md :: 唯一权威记录` | `device_log_event` 表 | 改 DLE 唯一记录主张 |
| `log-signal-stream` | concept | 跑测期异常事件权威流 | `docs/adr/ADR-0018-infrastructure-layer-framework-adoption.md :: \`log_signal\` 是异常事件权威流` | `SignalEmitter` / outbox | 改权威流措辞或旁路上报 |
| `dedup-pipeline-behavior` | concept | 多厂商并列 dedup/merge **行为与分区** | `docs/adr/ADR-0032-unisoc-mtk-parallel-dedup-pipelines.md :: ### D1：两条并列流水线，禁止交叉混用` | `dedup_scan` 分流；结构面见 0033 §1.1 | 混平台/混流水线提案 |
| `center-storage-model` | concept | 中心存储布局 / 方案 C 存储模型 | `docs/adr/ADR-0025-phase4-architecture-alignment.md :: ### D4: 日志归档——三阶段（搬运 + 汇总去重 + 分类提取）` | `devices/` `dedup/` `jira/`；`2026-storage-roles-and-aliases.md` | I-12 重排确认后修订 0025 |
| `R-merge-locus` | relation | `Merge ─executed_at→ 控制面实例`（现状 flock 绑定） | `docs/adr/ADR-0027-control-plane-horizontal-scaling.md :: 7. **merge（\`run_merge_sync\`）为实例绑定操作**` | `run_merge_sync`；`merge_instance_bound` WARN | 启用多实例互斥 / B1 / merge 成吞吐瓶颈 |
| `R-merge-consumes-log` | relation | `Merge ─consumes→ 日志事件/scan 产物` | `docs/design/2026-scan-upload-merge-contract.md :: ## 控制面 merge` | scan→upload→merge 契约；行为面 0032 | 契约改输入集或完备性单位 |
| `R-tool-hosted-by-tier` | relation | `工具实现 ─hosted_by→ Tier(1/2/3)` | `docs/adr/ADR-0033-tool-kit-ecosystem-integration.md :: ### D1：确立严格的三层工具宿主分类与生命周期隔离` | SAQ / Host 进程 / PlanStep | 新宿主层或跨层混跑 |
| `flash-tool` | concept | 刷机工具族（补 0033 分类空洞） | 本表登记；提权面 `docs/adr/ADR-0037-agent-host-privilege-boundary.md :: D5 flash 链运行时提权收敛`；编排面 ADR-0020 | `flash_firmware` / `flash_preflight`；`stp-agent-priv` | 0033 修订正式写入 D1 表；新刷机二进制入仓 |
| `jira-post-completion` | concept | 后处理闭环：策略/草稿/审计 vs 客户提交器 | `docs/adr/ADR-0012-post-completion-pipeline-jira-automation.md :: 第 1 层（✅ 已实现）` | 平台第 1 层已实现；客户 CLI → 0033 Tier1 包 | 第 2–3 层落地或客户脚本回渗 service |

> 未预填全量 Semantic Inventory。新双标：先加表行，再改域内文档——禁止先写第二份「唯一权威」散文。

---

## 6. ADR 头部字段与补齐策略

- 模板增补（见 `docs/adr/README.md`）：`- 归属域：semantic-ownership <key>`（可选；触碰/新建时补）。
- **不做**一次性全库补齐；S15③仅对已写字段生效（字段即触发器）。
- ADR identity 不可变：失去主导权时处置档仅 `Superseded` / `Scoped` / `Historical`——**不含** Rename / Merge / Renumber（N5）。

---

## 7. 本草案附带的域内措辞降级（nits；非决策改写）

| 位置 | 原风险 | 本草案动作 |
|---|---|---|
| `ADR-0021`「引用 / 关联」中对 ADR-0020 的括注「`plan_snapshot.script_meta` 作为权威」 | 被读成 X1 第三权威 | 降级为：派发冻结副本（快照隔离，ADR-0020），**非独立权威源** |
| ADR-0033 D1 表无 flash | 分类学空洞 F-5 | **本表** `flash-tool` 行补登记；0033 正文表行留给后续 Accepted 修订，本单不扩 Phase 2 |

---

## 8. 非目标

- 不替代 #2108 存量 ADR 收益台账。
- 不裁决 ADR-0033 Phase 2/3 实现、不改脚本版本树。
- 不合并/重编号 ADR。
- Y1（#2631）/ Y2（#2629）机制上受益于本表，**不在本单收口范围**。

---

## 9. 修订记录

| 日期 | 变更 |
|---|---|
| 2026-09-18 | 初稿：#2546 方案 A + Ownership Authority only + X1/X2/X3 + Tool/Script/Adapter + 四类嵌入面 + flash 行 + S15 表内判据 + 预填 12 行 |
