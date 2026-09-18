# 跨 ADR 语义所有权层立项评审（#2546 · claude-code · 7f3504）

## 范围与独立性

- 任务：按 [#2546](https://github.com/DUElost/stability-test-platform/issues/2546) 的「分派提示词」，
  对**是否建立跨 ADR 语义所有权层、以及立成什么形态**做独立只读评审（执行契约 Mode C：N 独立评审 → N Findings → 去重汇聚）。
  评审对象 = 本单正文的 X1–X3 / F-0–F-6 / 方案 A·B·C / F1–F7，加上正文评论轮补充的 N1–N6（含其「不可采信项」声明）。
- Harness：claude（Claude Code）；会话 `ce9f9e96-57d9-4dbc-bf8b-15491b7f3504`（末六位 `7f3504`）；
  Execution `review-semantic-ownership-claude-2`（role=`review`，test_impact=`none`）。
  因 2546 已有在窗 Execution（`review-semantic-ownership-zcode`），declare 按 §3.4 以 `--force` 放行并留 `[WARN]`——
  依据是本单正文自己的落盘指令「各自 declare + PR」，即 Mode C 的并行边界已由立案方显式确认。
- **审查基线：`ee75d1732`（评审时 `origin/main`）。** 本单写作基线为 `cebde359`，本稿全部数字在自身基线上重测。
  评审期间同题 sibling 稿（cursor，PR #2692）合入 `main`——**本稿在该 PR 合入前未读其内容，也不引用其结论**；
  提及其存在仅为解释「同目录下为何还有一份同题稿」。本稿所有结论来自本稿附录 A 的命令输出。
- 只读边界：**未**改任何 ADR / 代码 / 测试 / 本 issue 结论区；未触碰生产库与生产控制面。
  写产物 = 本稿（+ 其 PR）。撰写日 **2026-09-18**；文件名前缀沿用本单指定的组名
  `2026-09-17`（与本单「落盘二选一」给出的形态一致，便于 synthesis 归组）。
- 口径纪律：凡是引用本仓文件的行号，均在本稿基线上 `grep -n` 取过；凡引用计数，均附命令与原始输出（附录 A）。
  本单 F-2/F-3/F-4 的数字按纪律自行重测，**不照抄**。

---

## 一、复验结果

| 项 | 结论 | 关键证据（本稿基线） |
|---|---|---|
| **X1** 脚本"唯一权威"三口径 | **字面确认，但性质判定需改写** | `ADR-0021:96`「### D4 — 平台 DB 是脚本内容唯一权威」／`ADR-0033:121`「DB script 目录（script catalog）仍是唯一运行时权威」／`ADR-0021:297`「ADR-0020 — Plan-Step 一次性切换（plan_snapshot.script_meta 作为权威）」。**加强**：`ADR-0033:134` 同篇内限定词脱落——「DB script catalog **唯一权威** + manifest 仅作发布格式」 |
| **X1（新，见 §二.2）** | **三处载体中两处不存在** | `plan_step.script_sha` 全仓仅文档命中；`plan_snapshot.script_meta[*]` 无此键。运行时代码的真载体是 `Script.content_sha256` |
| **X2** 日志域「四层权威并存」 | **字面只有两层，表述过强** | `ADR-0028:44`「**唯一权威记录**」、`ADR-0018:315`「`log_signal` 是异常事件权威流」逐字命中；但 `grep -n 权威` 在 `ADR-0032-*.md` / `ADR-0025-*.md` 均 **0 命中**——0032 的"权威"是由 `ADR-0033:18` 与 `docs/DOC-MAP.md:99` **转述**的 |
| **X3** merge 归属三处不一致 | **确认，且同文自相矛盾** | 提案 `:11-12`「两项均属 ADR-0025 域，不新立 ADR」／台账 `:247`「（**ADR-0025 / ADR-0033** 域）」／`ADR-0027:130`「**B1** = 把 merge 迁到 worker/Agent（**归 ADR-0033**…）」。**加强**：同一份提案自己的裁决记录 `:73`「B 作为目标态，**必须与 ADR-0033 一起推进**」与 `:11-12` 互斥 |
| **F-0**（纠偏 ADR-0039 域归属） | **确认** | `ADR-0039:3` 状态=Proposed；`grep -c "日志链\|日志事件\|log chain"` = **0** |
| **F-1**（在场但过时的窗口） | **确认存在，但本单对它的描述两处有误** | 见 §二.1：该句在 `## 总原则`（`AGENTS.md:15-16`）**不在** `## 硬不变量`（`:23` 起）；且它**没有** S11 锚 |
| **F-2** 权威声明密度 | **逐字复现** | 0033 **13**／0026 7／0034 6／0018 6／0021 4／0029 3／0028 1（附录 A.1） |
| **F-3** 引用计数已被代码侧稳定外键 | **结论成立，数字不可复现** | 本稿三种口径实测 187/126/76/59（全仓）、68/63/35/9（代码面）等，与本单记载的 132/129/71/25 不符（附录 A.2）；机制亦需更正——S14 对未知 ADR 号是 **fail-open**（§二.3） |
| **F-4** 脚本域膨胀 | **逐字复现** | 34 族 / 179 版本目录 / 321 `.py` / 107,464 行；`flash_firmware` 22 + `flash_preflight` 5 = 27 |
| **F-5** 分类学空洞 | **确认，并补一条反向证据** | `grep -c "刷机\|flash" ADR-0033` = **0**；而 `flash_firmware` 的**代码**在引 `ADR-0033 §D2`（`v1.3.11/flash_firmware.py:69,850`）——最大族在代码面活跃引用该 ADR，该 ADR 的分类学里却没有它 |
| **F-6** 命名轴并存 | **确认，且多于四套** | `Tier 1/2/3` 仅 1 篇（0033）；`T0–T3` 3 篇；`M1–M7` 17 篇；`优先级：P0/P1/P2` 38 篇（P0 10／P1 17／P2 7／带括注 4）。另有 #724 的 6 域分类与本单的 4 域名单 |
| **ADR 总数** | **基线漂移** | 本单写 45，本稿实测 **48**（附录 A.3）；`docs/adr/` 一个自然日内仍在增长 |
| **同病复发（新）** | **当日实证** | `docs/notes/bug-fix/2026-09-18-authority-docs-drift-2661-2662.md`（#2661/#2662）：env 键与站点样例两面复发，且给出本仓既有处置范式=**就地修 + 单点判据 + 变异自证** |

---

## 二、对本单事实的更正与新证据

### 二.1 F-1 的章节归属写错，且错被 ADR-0039 复制了三遍

本单 F-1 写「`AGENTS.md` **硬不变量**现写「已发布脚本版本不可原地修改**或删除**」」。实测：

- 该句在 `AGENTS.md:15-16`，位于 **`## 总原则`**（`:6` 起）；`## 硬不变量` 从 `:23` 起，共 8 条（`:25-33`）。
- 这不是本单的笔误，而是**被 ADR 复制的**：`ADR-0039:16`「`AGENTS.md` 硬不变量规定「已发布 … 不可原地修改或删除」」、
  `:66`「将 `AGENTS.md` 硬不变量与 ADR-0020 的绝对不可变，收窄为两段式」、`:149`（落地义务）
  「**本 ADR 裁决通过后，同 PR 修订 `AGENTS.md` 硬不变量**与 `check-script-version-immutability.py` 判据」。
- 因此 F-1 窗口的真实形态比本单写的更糟：实施者按 `:149` 去改**硬不变量节**，会**找不到**那句话；
  真正过时的 `:15-16` 原地留存 → 「裁决已落地 + 门禁全绿 + 未过时的旧句仍在教读者」。这是"引用完整性 ≠ 语义完整性"（N4）的一个当场实例。
- 且该句**没有** S11 锚：`HARD_INVARIANT_ANCHORS`（`tools/dev/check_governance_surface.py:270-282`）11 条里，
  与脚本相关的只有「`default_params` 不可原地修改」（对应 `:32` 那条），「不可…删除」无锚 →
  改写或整条删除都**零门禁信号**。注意 S11 实现（`:285-293`）是对 **AGENTS.md 全文** `re.search`，本身不限定章节。

### 二.2 X1 的三处「权威」里，两处指向不存在的载体（本稿新增，建议进裁决）

本单把 X1 定性为「三个限定词并存，评审方判不出信哪条」。对着代码核之后，真实情况更硬：

1. **`plan_step.script_sha` 不存在。** `ADR-0033:122`「保 ADR-0023 `plan_step.script_sha` 溯源」、
   `ADR-0039:48`「可 pin（`plan_step.script_sha`，ADR-0023）」、`ADR-0039:97`「`plan_step.script_sha`、`step_trace`、`job_instance` 等历史事实仍完整」
   —— 全仓 `git grep -n "script_sha"` 只命中文档（附录 A.4）。`PlanStep` 只有
   `script_name` / `script_version`（`backend/models/plan.py:101-102`），无任何 sha 列；
   `step_trace`（`backend/models/job.py:96-115`）也没有脚本标识列，只有 `step_id` + `step_metadata`。
2. **`plan_snapshot.script_meta[*]` 不存在。** `ADR-0021:99`「后端用 `plan_snapshot.script_meta[*].content_sha256` 对账」、
   `:45`「与 `plan_snapshot.script_meta` 比对」、`:297`（引用行）—— 实测快照的键是
   `plan` / `steps[]`，每个 step 的键为 `nfs_path` / `param_schema` / `default_params` / `params` /
   `timeout_seconds` / `stall_seconds` / `retry` / `enabled` / `sort_order`
   （`backend/services/plan_dispatcher_core.py:525-580`），**没有 sha、没有 `script_meta` 层级**；
   生产者 `_fetch_script_metadata` 只 select `name/version/default_params/param_schema/nfs_path`
   （`backend/services/plan_dispatcher_sync.py:246-268`）。
3. **真载体是 `script.content_sha256`（DB 活行）。** `expected_scripts_for_run()`
   （`backend/services/precheck/scripts.py:12-43`）的 docstring 自陈「Build `[{name, version, sha256, nfs_path}]`
   **from plan_snapshot ∩ Script table**」——快照只提供 **(name, version) 键集**（`:14-16`），
   sha 取自 `Script.content_sha256`（`:24`、`:36`）；模型定义 `backend/models/script.py:19`。

**含义**：`ADR-0021:96` D4「平台 DB 是脚本内容唯一权威」是三者中**唯一在运行时成立**的表述；
另两处是**引用勘误**，不是"另一种限定"。据此，F4 里对 X1 的裁决建议（"给三处各发一个限定词"）应改写为：
先校正引用，再把真载体写进表——否则会把两个不存在的载体固化进权威表。
顺带：`ADR-0039:97` 的 D4 论证（删除后历史溯源仍成立）**结论可能仍成立，但引用不成立**，应重锚到
`plan_step.script_name/version` + `plan_snapshot.steps[]` + `step_trace`，否则该论证在评审时无法被机读复核。

### 二.3 F-3 的机制要更正：S14 不是「击穿变红」，是 **fail-open**

- `check_adr_code_refs`（`tools/dev/check_governance_surface.py:767-790`）对探测到的每个引用做
  `published = adr_versions.get(num)`，`:781` `if not published: continue` ——
  **ADR 号不认识就静默不约束**；而 `adr_versions` 只登记**头部带版本**的 ADR（`:1348`）。
  即重编号/合并后，旧注释不是"红灯"，而是**不再被任何判据覆盖**（覆盖缺口永久化——这正是 `#2249` 当时要修的病）。
- 另：F-3 说「ADR 号已是代码侧稳定外键」成立，但强弱要分清——`ADR-0033` 在整个 S14 扫描面
  （`backend` + `frontend/src`，排除冻结脚本目录）**只有 1 个文件 1 行命中**：
  `backend/services/dedup_scan.py:888`，且那行正是 X3 的 merge 归属注释。也就是说，
  **权威声明密度最高的 ADR-0033，在代码面几乎没有被引用的落点**，S14 对它近乎空转。

### 二.4 X2 的真实缺陷是「转述」，不是「四层并存」

`ADR-0032` / `ADR-0025` 全文 `权威` **0 命中**（本稿命令），X2 的四层在字面上只有两层自述。
`ADR-0033:18`（v1.1 变更记录）与 `docs/DOC-MAP.md:99` 的转述是**第三方说法**。
这直接推翻了「同一概念 ≥2 owner → 红」这类全库扫描判据：它会把「0033 转述 0032」也算成 0032 的一次 owner 声明（假阳性来源）。

### 二.5 F-5 要精确到"哪条边无主"

ADR-0033 §5.1（`:152`）用 `backend/agent/scripts/` 的版本目录数当"账目"，说明其「工具族」= **仓内脚本族**；
但 D0 的措辞是「**外部工具**源码不入主仓」（`:81`）。两个用法同篇并存，且没有任何一处定义
「哪些仓内族算工具族」——`flash_firmware`（22 个版本目录，全仓第一）与 `flash_preflight` 从未被归入或排除。
所以 F-5 不是"分类学写漏了一个词"，而是**关系（族 → 归类）无主**，它决定了 D0/D2 的准入判据是否适用。
补充：flash 的**提权**维度已有主（`ADR-0037:96-102` D5 明写 `flash_preflight`/`flash_firmware`），
说明缺的是**工具域内部的归类关系**，不是 flash 无任何归宿。

---

## 三、方案 A / B / C 表态

### A（最小方案）——**Needs-revision**；修掉下列四条后为 Accept-with-nits

**阻断 1（A①）：不得挂「冲突时以本文为准」这类内容裁决条款。**
赞成 N1 的自我修正，且本稿给出它要的仓库前例：
`docs/DOC-MAP.md:7` 自设「本页只保留三样常驻必需品：阅读顺序、**文档分层定义与登记簿**、**权威归属**」，
`:9`「冲突时以**代码与测试**为准」，`:115-121`「## 权威 vs 归档」按**目录粒度**归属；
`docs/design/2026-storage-roles-and-aliases.md:4`「冲突时以本文 + 代码为准」与
`docs/development/ai/execution-contract.md:3`/`:188`「本文是 Execution Contract 唯一权威源 … 冲突时以本文为准」
已经是**两份内容裁决权**。新表再挂内容裁决权，会出现"两份文档都自称内容以它为准"——就是本单要治的病。
（本单正文评论里写的「该类条款现仅 3 处」与本稿计数不符：本稿口径下**4 文件 5 处**，附录 A.5。）

**阻断 2（A③ 判据②）：不能「复用 S2」。**
S2 = `check_links()`（`:147-163`），只验 markdown 相对链接目标存在、跳过代码块与 `http(s)/mailto/#`，
**从不解析 `path:line`**。锚的正确形态是本仓两条既有先例：
S11 的**原文锚串**（`:268-282`，注释直言"改写措辞必须连锚一起改"）与
S5x 的「必须是某 job 的真实 step name」锚（`:17`、`:1066-1115`，`#2445` 的教训就是**弱锚=假绿**）。
⇒ 锚 = **文件 + 唯一文本模式**（命中 0 处红、命中 >1 处也红——歧义同样会让"owner"读错）；
行号只留在表里作人读跳转提示，**不进判据**。

**阻断 3（A③ 膨胀上界）：不得把新表塞进 S6 `RESIDENT_BUDGETS`。**
S6 的语义是**常驻入口**（启动上下文）行数/字节预算：表体 `:202-223`、实现 `check_resident_budget` `:226-236`、
调用面 `:1280-1286` 对表内每个条目无条件 `open()`。新表是 `docs/design/` 下的**按需**文档（`DOC-MAP` 自身也不在该表内），
塞进去会让「常驻」二字失真——用一个语义错位的机制去防膨胀，正是本单要治的病。改法二选一：
①（推荐）行数上界写成**表内自卡**（S15 顺手判"本表 ≤ N 行"，超出须合并或迁移）——零新机制；
② 显式把 S6 语义改写为「受预算的治理文件」并同步注释/设计文档（成本更高，收益相同）。

**阻断 4（F4-X1 落点）：裁决建议必须换成真载体**（见 §二.2），并把两处不存在的引用登记为勘误。

**建议（非阻断）**：
- `归属域` 字段**按需触发**（见 F6），且要意识到 `docs/adr/README.md:25-45` 的模板是**散文**、无门禁
  （`优先级/目标里程碑/决策者/标签` 在 check 脚本里只出现在自测样例中）——加字段不等于有判据，
  这是 `#2662`「样例自称完整而无人检查」的同型。
- 新表定位写清楚是**归属索引**：只从 `DOC-MAP §权威 vs 归档` + `AGENTS.md 按需入口` 各加**一行指针**进入
  （`DOC-MAP:7` 自设三样常驻必需品，不要顺手把它变成第四样）。
- 「表放新文件 vs 放进 DOC-MAP」两者皆可：我倾向新文件（DOC-MAP 有自设宪章且已 121 行），
  代价是要接受"又一份需要维护的 Living 文档"；若 owner 取最小，放 DOC-MAP 新小节亦可，但须同 PR 改 `:7` 那句宪章。

### B（三层 Core Semantics → Invariants → Domain ADR + 全量 Semantic Inventory）——**Needs-revision**

- 第一步「从 45（今 48）篇抽全部名词」与本稿实测规模不成比例：本稿口径下
  「唯一权威/权威源/single source of truth」型声明共 **11 处 / 8 篇**（附录 A.6），其中**真需要收口的是 2 处**
  （X1 的引用面、X3），另 1 处（X2）是"缺汇总"。对全库名词做 inventory 的成本高出两个数量级，收益面却只有这些。
- 可并入 A 的两条：①「**复议触发器**」列的表述取 B 的（A 已含该列，措辞按 B 收紧）；
  ② 五问决策树**不并入本单**——它的落点是 `tools/dev/ai_work.py declare` 的在窗查重（执行契约 §3.4），
  会改变**所有 harness 的领单动作**、属 ADR-0034 域，应单独立项（本单"不做 Accepted 裁决"的纪律同样适用于它）。
- **N6 的五层定义（Registry / Semantic Ownership / Domain Authority / ADR / Code）建议不并入**：
  F-6 已实测本仓至少 6 套命名轴并存（Tier / T0–T3 / P0–P2 / M1–M7 / #724 六域 / 本单四域），
  再加一层词汇是把 F-6 的问题放大而不是解决。它要表达的分工用一句话就够：
  **索引答"谁拥有定义权"，被指向的域内文档答"事实是什么"**。

### C（不立项，靠 #530 式逐对收口）——**Needs-revision（作为全局答案不成立），但内核保留为默认处置**

- 不成立的两条实证：① X1 是**同一篇 ADR 内部**的限定词漂移（`ADR-0033:121` vs `:134`）——逐对收口天然覆盖不到同篇；
  ② X3 在 3 天内被 3 个文档各写一遍，且**其中一份自己前后互斥**（提案 `:73` vs `:11-12`），逐对收口未收敛。
- 但其内核应保留：**每一处新冲突先就地写清**（`ADR-0011:32` 形态是有效先例），
  只有「跨域 + 有复发记录」的条目才登记进表。也就是说：表是**索引**，不是**处置机制**。
- 边际成本量化：X1 三处各自已消耗过评审/变更记录（`ADR-0033` v1.1 的变更条目本身就在处理权威分家），
  而本稿对 X1 的重新定性（§二.2）说明**这些文件里的表述从未与代码对过账**。

---

## 四、F1–F7 逐条

### F1 立项必要性（要量化判据）

本稿判据分三档，全部要求"有人照它行动会做错"才算真冲突：

| 档 | 条目 | 判据 | 计数 |
|---|---|---|---|
| 真冲突（会做错） | X1 引用面（两个不存在的载体被当成权威）、X3（同文互斥且三处归属不同） | 按任一处行动会落到不存在的字段/错误的 owner | **2** |
| 组织缺陷（不影响行动） | X2（四层都对，缺一处汇总） | 读者要多跳两次才能得到全景 | **1** |
| 非冲突 | `plan_snapshot` 的冻结语义、DB 的内容权威 | 已在代码里成立 | — |

⇒ **足以立一张 8–12 行的索引，不足以立全量 inventory。**
补一句本稿的独立判断：这张表的收益**主要不在 owner 列**（owner 基本是人工判断，且多数条目一轮就能读完），
**在"代码真源 file:line"列**——X1 的两个不存在载体，正是"填这一列时必须对着代码核"才会暴露的问题。
所以 A 的最小形态应当**保留并强化该列**（要求指向真实符号，不写自由文本），而不是把它当装饰。
ADR-0011:32 式的"就地写清"对 X1 **不适用**：X1 缺的不是一句话，是三个载体里两个不存在。

### F2 会不会长成第 5 份平行权威

- 四分法（回答"边界怎么划"）：
  **DOC-MAP** = 目录粒度权威/归档 + 阅读顺序（`:7`、`:115-121`）；
  **AGENTS.md 硬不变量** = 行为红线（有 S11 锚）；
  **execution-contract** = 执行语义唯一权威源（`:3`，其与规范附录的上下位关系见 `:7`）；
  **storage-roles / scan-upload-merge-contract** = 各自域的内容权威（`storage-roles:4`）；
  **新表** = **概念/关系粒度的归属索引**——不含内容、不含冲突裁决条款、不 supersede 任何人的表述权。
- 防膨胀的可自证判据（比行数更有判别力）：**若一年后该表仍 ≤ 约 15 行、且每条都有"复议触发器"记录，它就没变成第 5 份权威；
  一旦出现成段的"概念解释"，即已失守**。行数只是上界，不是有效性判据（F7）。
- 硬边界：**不得新增"细节解释"列**；细则一律以指针指向 owner 文档的节。

### F3 判据可机读性（含假阳性面）

- 判据①「同一概念 ≥2 owner → 红」：**表内**可机读且 FP-free（唯一键由表自己声明，不解析散文）；
  **从散文推导 owner 不可机读**，且必然把两件事判成冲突：分层权威（X2 四条都对）与**转述**
  （`ADR-0033:18` 转述 0032，见 §二.4）。⇒ 判据必须收在表内。
- 判据②锚：见阻断 2。**假阳性面**不是"书写变体"（钉一种写法即可），而是**否定/引用用法**。
  本稿实测两条必红的假阳性：
  `ADR-0036:171`「把契约塞进去会让一份可观测性 ADR 变成投递权威源，主题错位，**形成两个平行权威源**」（这是**驳回方案 C** 的行）、
  `ADR-0011:32`「两者是上位/下位关系，**不构成平行权威源**」（这是**边界声明**，恰是本单推崇的写法）。
  关键词门禁若把它们判红，其结果是被当噪声绕过而整体失效 → 必须有**行级豁免标记 + 理由**，学 `#2249` 放宽 S14 判据的先例。
  再进一步：**豁免条数本身要计数**（增长即提示扫描面该复审）——否则豁免会变成新的静默绕过面。
- 判据③「ADR bump 涉及归属域行 → 表须同 PR 出现」：仅在字段**在场**时生效（见 F6）。
- 结论：三条判据全部**收在表内 + 命名扫描面**可行；任何"扫全库散文"的版本应直接放弃（做不到就降级为人工清单，并把代价写明为"依赖评审人自觉"）。

### F4 X1 / X2 / X3 的单行裁决建议

- **X1**：owner = **`ADR-0021:96` D4**（"平台 DB 是脚本内容唯一权威"是唯一与运行时代码一致的表述：
  `precheck/scripts.py:36` 取 `Script.content_sha256`）。
  `ADR-0033:121` 保留但降级为**派发/版本选择**的运行时权威（把"运行时"钉在 D3 自己的语境里，且 `:134` 补回限定词）；
  `ADR-0021:45/:99/:297` 与 `ADR-0033:122`、`ADR-0039:48/:97` 的 `plan_snapshot.script_meta[*]` / `plan_step.script_sha`
  登记为**引用勘误**（前者 → `Script.content_sha256`；后者 → `plan_step.script_name/version` + `plan_snapshot.steps[]`）。
  **不改任何决策内容**，只改指向。
- **X2**：一行四列即可，owner 依次 `ADR-0028`（DLE 台账）/ `ADR-0018`（`log_signal` 流）/
  `ADR-0032`（行为与分区 merge）/ `ADR-0025`（存储模型），并在"派生面"列点名 `log_observation` 与 `job_log_signal`。
  **不要**为此新立"上位/下位"文本：X2 四层本来没有互相否定的句子（`ADR-0032`/`ADR-0025` 连"权威"二字都没写，§二.4），
  缺的只是一处汇总；照 `ADR-0011:32` 写反而会把两层本来无争议的关系升格成需要裁决的上下位。
- **X3**：owner = **`ADR-0027:118-134` 清单第 7 条**（它同时含限制现状=本机 `flock` 与解除路径 B1/B2 及其归属）。
  提案 `:11-12` 与台账 I-13（`:247`）改为指向该条。**与 B0 相容**：B0（提案 `:75`）裁的是"何时做"（不提前），
  第 7 条定义的是"谁定义它"——把归属挂在登记条上，恰好避免"在 0025/0033 里各写一遍解除时机"。
  另：提案 `:11-12` 与自身 `:73` 的互斥应同 PR 消掉（这是本稿支持"要有一张表"的最硬一条：连单文件内都没人负责一致性）。

### F5 F-1 窗口的处置时序

**不等 0039 裁决**，三步（落点建议，不在本单范围内）：

1. **先把锚挂上**：把 `AGENTS.md:15-16` 那条补进 `HARD_INVARIANT_ANCHORS`（`:270-282`），
   label 建议「已发布脚本版本不可删改（总原则）」，并在 S11 docstring（`:285-293`）写明"锚取的是 **AGENTS.md 原文串**，
   不限于硬不变量节"（实现本就如此，只是没写下来）。成本约 3 行。
2. **同批校正 `ADR-0039:16/:66/:149` 的"硬不变量"措辞为"总原则"**（ADR-0039 仍是 Proposed，属其自身修订）。
   不校正则 `:149` 的落地义务会把实施者指向错误章节（§二.1）。
3. **「新增 S1x 校验锚点与 Accepted ADR 语义一致」不可行**：语义一致性无机械判据，硬做只能退化成关键词比对——
   那恰好就是 F3 里要避免的那种假阳性制造机。**可实现的替代**是"锚串变更必须与被改的 AGENTS.md 同一 PR"：
   它不证明语义一致，但把**静默改写**变成**可见 diff**（S11 已具备这个性质，只差把这条挂上）。

### F6 多 harness + 存量补齐策略

- `归属域` 字段 = **触发器，不是义务**：S15 只在字段**在场**时校验一致性 + 新 ADR 按期强制（**存量 48 篇不补**，
  也不靠"触碰即补"的口头约定——口头约定无判据，正是 `#2662` 的病）。
  现成先例：S10 的 `NOTE_HEADER_CUTOFF = "2026-09-05"`（`:794`、`:808`）——一条常量按日期切存量，零迁移成本。
- 与 ADR-0034 Execution 登记的衔接：本表属共享元文件面，沿用「同一时间只由一个 Execution 修改」的既有串行规则，
  **不新加规则**；表的单次写入只有一行，冲突面小于 `docs/DOC-MAP.md`。
- 与 `AGENTS.md` 按需入口的衔接：加一行指针后 `AGENTS.md` 为 75 行 / S6 预算 80 行（`:203`），仍有余量。

### F7 验收可测性（本单给的判据做不到）

本单建议「出现新的双标时 S15 必红」——**按 A 的现写法做不到**（S15 只读表，无法判定新散文与旧文是否冲突）。
替换为三条可测判据：

1. **S15 自带变异测试，≥5 条变异必须变红**（概念行重复、锚指向不存在的文件、锚命中 2 处、
   ADR bump 而表未同 PR 出现、豁免无理由）。范式直接照当日落地的 `#2661/#2662`：
   变异 M1–M7 / N1–N5 逐条变红 + 还原后基线绿；其中 `M1 首跑曾假绿`（标记出现在链接 URL 里）促成了结构性修法
   （先剥 `[text](url)` 的 url 再判定）——**这条教训对 S15 的锚判据同样成立**（锚写在 markdown 链接里 vs 写在文本里，判别力不同）。
2. **X1/X2/X3 各收敛为表内一行**，且锚唯一、`代码真源` 列指向**实际存在**的符号。
   §二.2 就是该列的第一次真实验收：填 X1 那一行时必须能核出 `Script.content_sha256` 而不是照抄 ADR 措辞。
3. **下一次跨域权威声明落成"一行 + 就地一行指针"**（而非新文档）——用连续 1–2 次此类变更观察。
- 不要用"表行数少"当验收：行数是**膨胀上界**（F2），不是有效性判据。

---

## 五、对评审输入补充 N1–N3 的表态

### N1：**选「Ownership Authority only」**（不给新表内容裁决权）

依据（仓库前例，非抽象论证）：
`docs/DOC-MAP.md:7`（自设"只保留三样常驻必需品"）+ `:9`（冲突时以**代码与测试**为准）+ `:115-121`（目录粒度权威归属）——
**归属这件事在目录粒度已经有主**；`storage-roles:4` 与 `execution-contract.md:3`/`:188` 则是两份**内容**裁决条款。
新表若同时声称"归属归我、内容也以我为准"，就制造了"两份文档都自称内容为准"的新双标。
同意 N1 后半段：**归属争议走 owner 人工裁决（同 #1557 的 D-1… 机制），裁决后只改表一行。**
这条不需要新造机制，Mode C 的 synthesis → owner 裁决 → 落地就是它的运行先例。
（另注：N1 引用的 #724 是 **issue**，不在仓库文档树内，故不是可门禁的先例；可门禁的同类形态是 `DOC-MAP:7/:115`。）

### N2：第一批行里应放哪几条边

**该进（每条都有实际漂移证据）**：

| 边 | 漂移证据 | 落点 |
|---|---|---|
| `脚本内容 ─权威载体→ {DB 活行 / 派发冻结副本}` | X1；且本稿实测**两个被引载体不存在**（§二.2） | owner=`ADR-0021 D4`，代码真源=`Script.content_sha256` |
| `merge 执行位置 ─实例绑定→ 宿主/实例` | X3；`ADR-0027:118-134` 已登记限制 | owner=`ADR-0027` 第 7 条 |
| `工具族 ─归类→ {外部工具 / 仓内脚本}` | F-5；27 个版本目录无归类，直接决定 D0/D2 是否适用 | owner=`ADR-0033`（需在 v1.3 补分类，本单只归 owner） |

**是伪需求（本稿建议不收）**：

- `ScriptVersion ─bound_to→ PlanStep`：已被 `ADR-0020` + `ADR-0023` + `plan_step.script_name/version`
  （`backend/models/plan.py:101-102`）唯一覆盖，且无漂移记录。
- `工具实现 ─executed_by→ 宿主`：`ADR-0033 D1` 三层宿主（`:86-94`）已定义且未被质疑。
- 判据：**一条边进表的前提是"有实际漂移/争议记录"，不是"看起来重要"**——否则表会立刻膨胀，与 A 的预填纪律自相矛盾。

### N3：白名单会不会让 S15 退化成"只检查自己写过的东西"

**会——如果白名单是"概念"**（表内自证，等价于只测自己）。改法：**白名单落在"扫描面（文件）"**：
S15 的声明面扫描只覆盖 **表自身 + `AGENTS.md` + 四份域内宪法**（`storage-roles` / `scan-upload-merge-contract` /
`execution-contract` / `DOC-MAP`）+ **ADR 头部行**，不做全库散文扫描。
这样：新增声明只要落在这几个面内就必被拦（不是"只查自己"），FP 面又可控（行级豁免 + 理由）。
最小补充判据 = **豁免条数计数**（见 F3）：豁免增长即提示扫描面需要复审，防止它退化成静默绕过面。

---

## 六、结论分级汇总

### 阻断（须改再落）

1. **A①**：新表**不得**携带「冲突时以本文为准」型内容裁决条款（取 N1 的 Ownership-only）。证据：`DOC-MAP:7/:9/:115-121`、`storage-roles:4`、`execution-contract.md:3/:188`。
2. **A③ 判据②**：不能"复用 S2"（`check_links` 不解析 `path:line`，`:147-163`）；锚必须 = 文件 + **唯一文本模式**（0/多命中皆红），行号不进判据。
3. **A③ 膨胀上界**：不得把新表塞进 S6 `RESIDENT_BUDGETS`（该表语义是**常驻入口**预算，`:202-223`/`:226-236`/`:1280-1286`）；改表内自卡。
4. **F4-X1**：裁决建议必须换成真载体（`Script.content_sha256`，`precheck/scripts.py:36`），并把 `plan_step.script_sha`、`plan_snapshot.script_meta[*]` 两处不存在的引用登记为勘误。

### 建议

5. **F5**：AGENTS.md `:15-16` 那条补进 S11 锚表 + 同批校正 `ADR-0039:16/:66/:149` 的"硬不变量→总原则"；不要做"锚点与 ADR 语义一致"的判据。
6. **F6**：`归属域` 按需触发 + 新 ADR 按期强制（照 S10 `NOTE_HEADER_CUTOFF` 形态）；意识到 README 模板字段本身无门禁。
7. **F2**：定位为"归属索引"，边界四分化（DOC-MAP 目录粒度 / AGENTS 行为红线 / execution-contract 执行语义 / 新表 概念粒度），指针各一行，不 supersede 任何表述权。
8. **F7**：验收改成"**变异测试 ≥5 条必红** + 三处各收敛一行 + 下一次跨域声明落成一行指针"。
9. **F3**：行级豁免 + 理由 + **豁免计数**；判据只收在表内与命名扫描面。
10. **N2**：三条边进表（脚本内容载体 / merge 实例绑定 / 工具族归类），两条拒绝。

### 观察

- **基线漂移在加速**：本单写 45 篇，本稿实测 **48** 篇（+2 前稿 → +3 本稿，一个自然日）。"只预填真在抖的 8–12 行"这条纪律比方案本身更重要。
- **同病在别的载体复发**：`#2661/#2662`（2026-09-18 落地）是 env 键与站点样例两面，处置范式是**就地修 + 单点判据 + 变异自证**——
  这既是"表有正收益"的证据（复发面在扩），也是"表不是唯一解"的证据（本仓的既有范式已经能收口）。
- **S14 的 fail-open**（`:781`）是给 S15 的现成警示：任何"用编号/路径当外键"的判据，都要先回答"外键失配时是红还是静默跳过"。
- **代码真源列是本表最不可省的一列**：本稿全部新证据（§二.2）都来自"填这一列时被迫核代码"，而不是来自对 ADR 措辞的阅读。

---

## 附录 A：本稿测量命令与原始结果（基线 `ee75d1732`）

### A.1 F-2 权威密度

```bash
grep -c 权威 docs/adr/ADR-*.md | grep -v ":0$" | sort -t: -k2 -rn
# ADR-0033:13  ADR-0026:7  ADR-0034:6  ADR-0018:6  ADR-0021:4  ADR-0029:3
# ADR-0046:2  ADR-0038:2  ADR-0019:2  ADR-0017:2  ADR-0045:1  ADR-0044:1
# ADR-0036:1  ADR-0028:1  ADR-0011:1  ADR-0001:1
```

### A.2 F-3 引用计数（三种口径，均与 F-3 原数字不符）

```bash
for n in 0020 0025 0028 0033; do
  echo "ADR-$n all=$(git grep -l "ADR-$n" -- . | wc -l) \
code=$(git grep -l "ADR-$n" -- backend frontend/src tools scripts tests | wc -l) \
no-frozen-scripts=$(git grep -l "ADR-$n" -- . ':!backend/agent/scripts' | wc -l)"
done
# ADR-0020 all=187 code=68 no-frozen-scripts=184
# ADR-0025 all=126 code=63 no-frozen-scripts=126
# ADR-0028 all=76  code=35 no-frozen-scripts=76
# ADR-0033 all=59  code=9  no-frozen-scripts=51
# 本单记 132/129/71/25 —— 两种合理口径下都对不上（结论"稳定外键"仍成立，数字不进判据）
git grep -n "ADR-0033" -- backend frontend/src ':!backend/agent/scripts'
# backend/services/dedup_scan.py:888:    B1（把 merge 迁到 worker/Agent，归 ADR-0033）。
```

### A.3 规模与状态

```bash
ls docs/adr/ADR-*.md | wc -l        # 48（本单写 45）
find backend/agent/scripts -maxdepth 1 -mindepth 1 -type d | wc -l        # 34 族
find backend/agent/scripts -maxdepth 2 -mindepth 2 -type d -name 'v*' | wc -l  # 179
find backend/agent/scripts -name '*.py' | wc -l                            # 321
find backend/agent/scripts -name '*.py' -exec cat {} + | wc -l             # 107464
find backend/agent/scripts/flash_firmware -maxdepth 1 -mindepth 1 -type d | wc -l   # 22
find backend/agent/scripts/flash_preflight -maxdepth 1 -mindepth 1 -type d | wc -l  # 5
grep -c "刷机\|flash" docs/adr/ADR-0033-tool-kit-ecosystem-integration.md   # 0
grep -c "日志链\|日志事件\|log chain" docs/adr/ADR-0039-script-version-immutability-narrowing.md  # 0
```

### A.4 X1 载体核验

```bash
git grep -n "script_sha" | grep -v test_wifi_optional   # 仅 docs 命中，代码 0
sed -n '101,102p' backend/models/plan.py                # script_name / script_version（无 sha 列）
git grep -n "script_meta" -- backend                    # 仅 3 个 dispatcher 的 script_metadata 变量
sed -n '525,580p' backend/services/plan_dispatcher_core.py   # 快照键：无 sha、无 script_meta 层级
sed -n '246,268p' backend/services/plan_dispatcher_sync.py   # select 只取 name/version/params/schema/nfs_path
sed -n '12,43p' backend/services/precheck/scripts.py         # docstring「from plan_snapshot ∩ Script table」；:36 取 Script.content_sha256
sed -n '96,115p' backend/models/job.py                       # step_trace 无脚本标识列
```

### A.5 内容裁决型条款

```bash
git grep -n "冲突时以" -- docs AGENTS.md
# 内容裁决型：storage-roles:4、execution-contract.md:3/:188、2026-adr-0025-log-flow-sequence:8、ADR-0026:102  → 4 文件 5 处
# 代码事实型：AGENTS.md:8、docs/README.md:4、DOC-MAP:9（"以代码与测试为准"）
```

### A.6 权威声明面与假阳性样例

```bash
grep -n "唯一权威\|权威源\|single source of truth" docs/adr/ADR-*.md   # 11 处 / 8 篇
sed -n '171p' docs/adr/ADR-0036-notification-delivery-semantics.md
# …会让一份可观测性 ADR 变成投递权威源，主题错位，形成两个平行权威源。（驳回方案 C 的行 → 关键词门禁必红）
sed -n '31,32p' docs/adr/ADR-0011-observability-and-alerting-evolution.md
# 两者是上位/下位关系，不构成平行权威源……（边界声明 → 关键词门禁必红）
```

### A.7 门禁机制与可挂载面

```bash
grep -n "S15" tools/dev/check_governance_surface.py            # 0 命中（S15 号位空闲）
python3 tools/dev/check_governance_surface.py --check          # [OK] …（阻塞项全绿：S1–S14、S5x）
sed -n '147,163p' tools/dev/check_governance_surface.py        # S2 = check_links（只验 markdown 链接目标存在）
sed -n '268,293p' tools/dev/check_governance_surface.py        # S11 锚表 11 条 + 全文 re.search 实现
grep -n "if not published" tools/dev/check_governance_surface.py   # :781 S14 fail-open
python3 scripts/run_gates.py --list                            # check:quick 含 gov-surface
grep -n "NOTE_HEADER_CUTOFF" tools/dev/check_governance_surface.py # :794 常量 / :808 使用（存量按日期切）
```

### A.8 其它

```bash
sed -n '1,16p' AGENTS.md           # :6 总原则 → :15-16 不可原地修改或删除 → :23 硬不变量
ls -la CLAUDE.md                   # symlink → AGENTS.md
grep -n "AGENTS.md 硬不变量" docs/adr/ADR-0039-script-version-immutability-narrowing.md   # :16 / :66 / :149
wc -l -c AGENTS.md                 # 74 行 / 5129 字节（S6 预算 80 行 / 8000 字节）
git grep -n "ADR-0033" -- backend/agent/scripts/flash_firmware/v1.3.11/flash_firmware.py  # :69 / :850
```

---

**总评：对方案 A = Needs-revision（四条阻断修掉后 Accept-with-nits）；对方案 B = Needs-revision；对方案 C = Needs-revision（内核保留为默认处置）。**
本稿是**裁决输入**：是否落 `2026-semantic-ownership.md` 与 S15、以及 X1/X2/X3 的最终 owner 归属，均归 owner 裁决。
