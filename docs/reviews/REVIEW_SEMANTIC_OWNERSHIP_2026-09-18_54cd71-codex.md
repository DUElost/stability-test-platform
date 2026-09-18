# 跨 ADR 语义所有权层立项 · 独立评审（Mode C / codex）

- **Harness / 会话**：`codex` · 会话后六位 `54cd71`（`CODEX_SESSION_ID=01a0b380-4c27-7241-98f1-b6382554cd71`）
- **Execution**：`review-semantic-ownership-codex`（`declare --force`，与在窗 `…-zcode` 并行——Mode C 即「先独立、后汇聚」）
- **评审基线**：`origin/main = ee75d17363b975ad8f09b53f577a3b9ef9b67d89`（`ee75d173`，2026-09-18 00:55 -0700）
  issue 声明基线 `cebde359`（2026-09-17 10:08 +0800）→ **相隔 501 个 commit / 约 30 小时**（本稿所有行号均按 `ee75d173`）
- **纪律**：只读——未改任何 ADR / 代码 / 测试 / issue 结论区；未触生产库与控制面
- **独立性声明**：所有结论自带复现命令（§9）。写作中对 `docs/**` 的 grep 会命中 main 上已合入的两份
  评审稿正文（`…_004377.md`、`…_ae8cd9-cursor.md`），**未以其文本为任何论据来源**；
  与既有稿重合处由 synthesis 去重，本稿在 §7 对重合项标注「收敛」，对其独有点标注「本稿新增」。
- **总评**：**A = Needs-revision · B = Needs-revision · C = Accept-with-nits（方向）**；
  issue 文书本身 **Accept-with-nits**（F-0/F-2/F-4/F-5/F-6 全部可复现；F-1 归节错误、F-3 数字不可复现、
  X2「四层权威」高估、「无任何机械保证」定性不准确——见 §1.2）。

---

## 0. 一句话结论

**立「层」不立「文档」**：问题是真的，但 A 的三条机制里有三条在本稿实测中失败（新表宿主、`file:line` 判据、
ADR 必填字段），而本仓**已有**承载该层所需的三件现成物——`§7.1 强制力覆盖图`（同形表格 + 维护棘轮）、
`tools/dev/source_anchor.py`（锚点强度：0 命中红、次数不符红）、`tests/test_*_claims_are_live.py`
（把可派生量从文字里删掉、改与真值对拍）。把 owner 行加进覆盖图、把判据写成一条对拍测试，
成本低于新建文档，且**不会**制造第 5 份平行权威——因为新建文档本身就是本单要防的那种病的最大风险源。

---

## 1. 对「已核实事实 F-0…F-6」的复验

### 1.1 完全可复现（照抄安全）

| 事实 | 本稿复测（`ee75d173`） | 判定 |
|---|---|---|
| **F-0** `ADR-0039` 属 Script 域 | 标题「脚本版本不可变契约收窄——直至零引用退役」；状态 `Proposed`、`v0.1（2026-09-13）`；`日志链/日志事件/log chain/device_log/log_signal` 合计 **0** 命中 | ✅ 采信，本稿未沿用其早期错误判定 |
| **F-2** 权威声明密度 | `0033=13 · 0026=7 · 0018=6 · 0034=6 · 0021=4 · 0029=3 · 0028=1`（逐值一致） | ✅ 复现 |
| **F-4** 脚本域 | **34 族 / 179 版本目录 / 321 个 `.py` / 107,464 行**；`flash_firmware` **22** 目录 = 全仓第一（次高 `monkey_setup` 15）；`unisoc_probe` / `unisoc_signal_trigger` 两族在场 | ✅ 逐值复现 |
| **F-5** 分类学空洞 | `ADR-0033` 全文 `刷机\|flash` = **0** 命中；`flash_firmware` + `flash_preflight` = **27** 个版本目录 | ✅ 复现 |
| **F-6** 四套命名坐标 | `Tier 1/2/3`（0033，7 命中）· `T0–T3`（0031）· `P0/P1/P2`（ADR 优先级）· `M1–M7` | ✅ 复现 |

### 1.2 必须纠偏的四处（都会影响裁决）

| # | issue 现写 | 实测（file:line） | 影响 |
|---|---|---|---|
| **纠-1** | F-1「**`AGENTS.md` 硬不变量**现写『已发布脚本版本不可原地修改**或删除**』」 | 该句在 `AGENTS.md:15-16`，位于 **`## 总原则`**（@6）；`## 硬不变量` 是 `:23-33`，其内与脚本相关的只有 `:32`「已存在脚本版本的 `default_params` 不可原地修改」 | 不是措辞问题而是**覆盖面问题**：S11 的锚表 `HARD_INVARIANT_ANCHORS`（11 条，`check_governance_surface.py:268-283`）里**没有**该句任何锚 → F-1 的窗口不是「门禁全绿 + 文本过时」，而是**该句被整条删除也不会红**（在场性都不校验）。修法顺序必须是「先挂锚 → 再谈语义一致」 |
| **纠-2** | F-3「0020 被 **132** 文件 / 0025 **129** / 0028 **71** / 0033 **25**」 | 四种自然口径都凑不出这组数：`git grep -l`（tracked@HEAD）= **188/127/77/60**；排除 `docs/notes` = **138/115/55/32**；仅代码扩展名 = **68/60/33/9**；`occurrences` = 372/295/123/182。唯一接近的是 `0020` 在「排除 notes @`cebde359`」= 133≈132，但同口径下 `0025` = 107≠129 | F-3 的**结论**（ADR 号是稳定外键、改名会击穿 S14）成立且重要（本稿独立确认 S14 在 `:738-786`，其排除脚本目录的理由「红灯但不可修的死结」在 `:47` 与 `:730`）；但**数字必须带口径**，否则不可采信。补充一条本仓特有的污染源：主工作副本含 8 个 `.wt/` 嵌套 worktree，裸 `grep -r . ` 会把 `ADR-0020` 放大到 **1943** 个文件（其中 **1670** 在 `.wt/` 下）→ 唯一可信口径 = `git grep`（tracked，HEAD，不含工作副本副本件） |
| **纠-3** | X2「日志域**四层权威并存**」 | 字面权威断言只有 **2** 处：`ADR-0028:44`（`device_log_event` = 唯一权威记录）、`ADR-0018:315`（`log_signal` 是异常事件权威流）。`ADR-0032` 与 `ADR-0025` 全文 `权威` **0** 命中——它们是「描述某一面」，不是「自称权威」 | 「四层并存」高估了冲突面；但**真缺口被 issue 说反了一半**：聚合面**已经存在**——`docs/design/2026-adr-0025-log-flow-sequence.md:283-297`「§5 观测 vs 归档：消费方矩阵（#527）」，逐消费方指明 RUNNING 读 signal / 终态后 DLE 权威，且该文件在 `docs/README.md:21`、`:102` 可达。**缺的不是汇总，是回指**：`ADR-0028` 与 `ADR-0018` 指向 `ADR-0025` 却**都不指向 §5**（实测两篇 `log-flow-sequence|消费方矩阵` = 0 命中）→ 收口 = 2 条指针，不需新表 |
| **纠-4** | 问题定性「**无任何一层**机械保证『一个事实只有一个 owner』」 | 有一层，且在执行域：`execution-contract.md §3.5`（:107-119）「**一个架构主题在同一时刻只能有一个权威 Decision Artifact**」「同一主题的第二份权威 ADR **不得合入**」，并已机械化到 `ai_work.py:55`（`ADR_FILE_RE`）、`:536`、`:756-766`（决策类未带 `--issue` → `exit 2` 默认拒绝）。**一手证据**：本稿 `declare` 未加 `--force` 时被 `[REFUSED]`，并逐条列出 4 个在窗 Execution | 定性应从「没有机制」改为「**机制只覆盖 Decision Artifact，未覆盖概念/关系陈述**」——这会直接改变 F1 的答案与 S15 的判据形状（复用而非新建） |

### 1.3 基线漂移实测（决定了「表里能写什么」）

- ADR 篇数：立案 45 → 第 4 轮 47 → 本稿 **48**（`ADR-0047-db-pool-and-connection-capacity.md` 等新增）；
  头部状态解析（S12 口径近似）：`Accepted 41 · Proposed 3 · Superseded 3 · Deprecated 1`。
- 行号漂移：DOC-MAP 中「AI Execution Contract 唯一权威源」那行，在 `cebde359` = **:106**、在 `ee75d173` = **:108**，
  **正文一字未改**（`git diff --numstat cebde359 HEAD -- docs/DOC-MAP.md` = +3/−1）。issue 正文写的 `:107`
  在两个基线上**都不命中**（`:107`@`cebde359` 是 external-tools 设计行，`:107`@`ee75d173` 是 ADR-0047 行）。
- 同类：`storage-roles` 现 **235** 行（issue 记 232），末改 2026-09-17 10:53。
- **推论（本稿新增）**：任何写进 owner 表的裸行号，其半衰期以**天**计。锚必须是文本模式或结构化定位，行号只配作人读提示。

---

## 2. 三处双标：复验结论是「三种不同故障」，不是一回事

| 处 | 复验到的原文（file:line） | 故障类型（本稿判定） | 1–2 行收口动作 |
|---|---|---|---|
| **X1** 脚本「唯一权威」 | `ADR-0021:96`「D4 — 平台 DB 是脚本内容唯一权威」；`ADR-0033:121`「DB script 目录（script catalog）仍是**唯一运行时权威**」；同篇 `:134`「DB script catalog **唯一权威**」（丢了「运行时」）；`ADR-0021:297`「ADR-0020 —…（`plan_snapshot.script_meta` 作为权威）」 | **限定语漂移**，不是 owner 争夺。且第三腿**无源**：`ADR-0020` 全文 `script_meta` **0 命中**，其 `plan_snapshot` 最小结构（`:181-211`）不含该键，唯一「唯一」句（`:43`）说的是 Plan 模型 | 代码已给出正确答案：`backend/services/precheck/scripts.py:12-37` 的 docstring 写明真值来自「**plan_snapshot ∩ Script table**」——快照只供 `(name, version)` 身份（`:16`），`sha256`/`nfs_path` **现取于 live `script` 表 ∧ `is_active`**（`:20-27`）。→ 一行覆盖三腿：**内容=0021 D4（`models/script.py:19`）· 身份=快照（`plan_dispatcher_core.py:524`）· 运行时可选面=0033 D3（`plan_dispatcher_sync.py:261`）**；`ADR-0021:297` 改为「快照冻结 `(name,version)`，sha 现取于 DB」并**去掉对 0020 的归属** |
| **X2** 日志域 | 见纠-3 | **聚合面存在但无回指**（可达性缺口），非归属缺口 | `ADR-0028:44` 与 `ADR-0018:315` 各加一句「消费方矩阵见 `design/2026-adr-0025-log-flow-sequence.md` §5」；两条表内行即可（`device_log_event` @ `models/device_log_event.py:24`；`job_log_signal` @ `models/job.py:175`）——**不得判为双标**，#527 的裁决形态就是「两条链、各自限定用途」（`notes/architecture/2026-08-29-log-observation-authority.md:8`） |
| **X3** merge 执行位置 | `notes/architecture/2026-09-15-center-storage-and-merge-locus-proposal.md:11-12`「两项均属 ADR-0025 域，不新立 ADR」；`notes/process/2026-09-15-device-log-flow-issue-backlog.md:247`「（ADR-0025 / ADR-0033 域）」；`ADR-0027:130`「B1（迁 worker，**归 ADR-0033**）」+ 变更记录 `:168` | **规范面 / 留档面混层**：三处中只有 1 处在 ADR（0027 的登记条），另 2 处在 `docs/notes`（#2042 定义的按需留档面）。三处 `权威` 字样命中 = **0 / 0 / 0** | 现态代码真源无争议：`dedup_scan.py:905` `_exclusive_merge_tool_lock`（本机 `flock`，工具目录取自 `STP_BACKEND_DEDUP_SCAN_SCRIPT` 父目录，`:882`）→ 布局/生命周期=0025、现态绑定=0027 第 7 条、B1 宿主=0033；**与 B0 完全相容**（B0 只裁「不提前」，见 `backlog:32`）。留档面两句改为指向 0027 第 7 条即可 |

> **本稿判定（对 F1 直接相关）**：X1 是措辞、X2 是链接、X3 是层次混用。**没有一处是「两个 ADR 抢同一个概念的同一面」**。
> 因此「三处双标」**不足以**证明需要一个新的 owner 文档；**足以**证明需要（a）把「归属/复述」纳入既有覆盖图棘轮，
> （b）一条能扫到存量的机读判据。量化：本稿实测三处收口 = 约 **6 行**文档改动；A 方案 = 新文档 + 模板字段 + 新规则 +
> 48 篇补齐 + `S1–S14` 区间同步（见 §3.6）。

---

## 3. S15 判据的可机读性：三组实测数字

### 3.1 「同一概念 ≥2 owner → 红」假阳性面 = 当下语料的 **>60%**

口径：`git grep -nE '唯一权威|权威源|single source of truth|以本文为准' -- '*.md' ':!docs/notes' ':!docs/reviews' ':!docs/archive'`
→ **30 命中**（立案方在第 4 轮补充中自记「20 处声明 / 3 处真冲突」，量级一致但未附口径）。分解：

| 桶 | 命中 | 例 |
|---|---|---|
| 索引/地图面**复述**（按构造必为复述） | 4 | `docs/DOC-MAP.md:99`、`:108`、`docs/adr/README.md:86`、`:87` |
| 否定式/否决式（**声明自己没有**权威） | 4 | `ADR-0011:32`「不构成平行权威源」、`ADR-0036:171`（记驳回理由）、`CONTRIBUTING.md:3`（「以仓库既有文档为唯一权威，**不在本文件复制**」） |
| 指向他文档的**指针式**复述 | ~10 | `03-frontend.md:19`+`:63`、`harness-adapters.md:72`、`repository-workflow.md:19`、`execution-contract-annex.md:3`、`ADR-0046:16`、`ADR-0034:22`、`2026-device-log-event-implementation-spec.md:283`、`skill add-api-endpoint:26` |
| **真 owner 断言** | ≈12 | `ADR-0021:96`、`ADR-0028:44`、`ADR-0029:301`+`:390`、`ADR-0033:134`、`ADR-0034:102`、`execution-contract.md:3`+`:188`、`aee/AGENTS.md:23`、`environment-variables.md:4`、`script-versioning.md:57` |

- **致命细节**：真 owner 断言里有 **3 组「同一篇内两句同概念」**（`ADR-0029:301` 与 `:390`；`ADR-0034:102` 与
  `execution-contract.md:3`+`:188`）。任何按「≥2 命中」实现的规则，**上线当天就会把自己写的第一行判红**。
- 结论：该判据必须换成「**表内**一行一概念 + 锚可解析」，即 issue 已被 §3.2/§3.3 否证后的最小可靠残量。

### 3.2 声明面门禁会**漏掉本单的直接触发点 X3**

`三处` 的 `权威` 字样命中：**0 / 0 / 0**（§2 表已列 file:line）。所以 issue 三.7 建议的门禁句式
（`唯一权威|权威源|single source of truth`）**不可能在 X3 上发红**——那三处争的是「属哪个 ADR 域」，压根不含「权威」二字。

补充测量：句式 `(属|归|属于) ADR-00NN … 域` 在**规范面**（`docs/{design,adr,development,operations}` + `AGENTS.md`）
命中 = **0**；全部 5 处命中都在 `docs/notes` 与 `docs/reviews`。→ **本仓的「归属域」裁决至今只发生在留档面**，
规范面里唯一的一条是 `ADR-0027:130`（写的是「归 ADR-0033」，不带「域」字）。
这同时解释了为什么「全库扫一遍」拿不到东西：**没有存量可扫**（也正是 `tests/test_source_scan_anchor_ratchet.py:20-22`
记录的失败形态——判据扫不到 offender 即红）。

### 3.3 「表内 `file:line` 不可解析 → 红，复用 S2」三重不成立

1. S2 只解析 markdown 链接：`check_links()` `:147-164`，无「行号」概念；
2. S2 **显式剥进行内 code**（`_strip_inline_code` `:143-146`，自测样例 `:1442`「行内 code 示例不算断链」）——
   而表格里的 `file:line` 按本仓书写惯例正是行内 code（`docs/reviews` 在扫描树 `LINK_TREES` `:168` 内，我写本稿时实测如此）；
3. 即使写成链接，`path_part = unquote(raw.split("#", 1)[0])`（`:157`）**丢弃 fragment**。

本稿用一次性探针**当场实测**了三种行为（探针文件即建即删，未入库）：

| 表内书写形态 | S2 实测结果 | 后果 |
|---|---|---|
| 行内 code：`` `AGENTS.md:15` `` | 不报（`:143-146` 先剥掉行内 code） | **静默假绿**——本稿自身有 **66** 处这种写法，S2 一处都看不见 |
| 链接形态 A：URL 尾部写 `#L9999` | 不报（`:157` 把 `#` 之后整段丢弃） | **静默假绿**——行号指向不存在也绿 |
| 链接形态 B：URL 里直接写「文件名 + 冒号 + 行号」 | 报 `[BLOCK] S2 … 断链`（见 §9 探针原样输出） | **当场假红**——`file:line` 的链接形态被当成不存在的目标 |

→ 「复用 S2 验 `file:line`」**双向皆错**：验不了行号（两种假绿），也接受不了行号写法（一种假红）。

→ 落点（**现成物，非自研**）：`tools/dev/source_anchor.py` 已把「锚点强度」做成不可拆的 API——
`SourceGuard.anchored(needle, expect=N)`（`:111-117`，0 命中 = `AnchorDrift`「用例已过期」；**次数变了也算漂移**）、
`assert_count(needle, expect)`（`:173-179`）、`assert_absent(…, why=…)`（`:154-160`，必须先声明锚点否则 `GuardMisuse`）。
这恰好就是 issue 补正里想要的「存在性 + 唯一性、行号不进判据」，且它区分**用例过期**与**防线回归**两类红——
S15 若自写解析器将拿不到这个区分能力。

### 3.4 非自反性（N3 的「会不会退化成只检查自己写过的东西」）

仓库已有答案，无需新发明：`tests/test_source_scan_anchor_ratchet.py:20-22` 的第三条红条件
——「**一个 offender 都扫不到 → 红（判据/路径失效）**」，其来源正是 #2639 自己少算 81 个文件的事故。
把该条件抄进 S15 即得最小补充判据：**上线时至少命中 1 处已知真实冲突**（本稿提供存量：`ADR-0033:121` vs `:134`、
`ADR-0021:96` 限定语、`ADR-0021:297` 无源引用——三者都能被锚文本判据稳定命中）。

### 3.5 落点应为「对拍测试」，不是「第 16 条 S 规则」

`#2663` 刚在 30 小时内落地了与本单同形的解法：`tests/test_alert_count_claims_are_live.py:1-28`——
「把可派生量抄成文字」的陈述，逐条与**当场派生的真值**对拍，真值来源列成表（= N3 要的显式白名单，已实现形态是
`LIVE_SURFACES`），并显式记录**已知假阴性**与「为什么没有带日期即豁免」（历史口径一律移进 `docs/notes/**`，
所以扫描面不需要绕过位）。这条路径的额外好处：

- 零新增门禁：`python -m pytest tests/ -q` 已在 PR 路径（`.github/workflows/ci.yml:130`），不触 S5x 的 GATES↔CI 锚点配对；
- 自动落入 #2639 棘轮管辖：该棘轮的扫描面是 `SCAN_DIRS = ("tests","backend/tests","backend/agent/tests")`
  （`test_source_scan_anchor_ratchet.py:30`）——**写在 `tools/dev/` 里的正则规则会绕开刚建立的锚点纪律**，
  写成 `tests/` 则被它接管。这是一个真实的、方向相反的落点差异。

### 3.6 两处实现陷阱（本稿新增，建议进 A 的落地清单）

1. **「膨胀上界用现成 S6 `RESIDENT_BUDGETS` 卡」不成立**：`RESIDENT_BUDGETS`（`:202-224`）13 个 key，
   **不含任何 `docs/design/*`** → 新表文件不在 S6 覆盖面内，卡不住。且预算循环 `:1280-1286` 对 key
   **没有存在性保护**（对比 `:1276` 的 `gates_src_path` 分支有 `os.path.exists`）→ **先加 key 后建文件 = `--check` 直接 FileNotFoundError**，
   gov-surface 整体崩（`check:quick`/`check:pr`/CI 三处同红）。膨胀上界应改由新判据自卡：**表行数 ≤ N**（一行代码，天然机读）。
2. **新增规则会撞一处已知的写死数字**：`docs/design/2026-08-governance-surface-protection.md:29` 把 L0 写成
   「确定性文本检查 **S1–S14**」；今日落地的 `notes/bug-fix/2026-09-18-gov-doc-drift-2659.md:70` 已把该区间
   记为 Revisit（「下次新增规则（S15）时会再次漂移」，触发条件=第三次复发）。实跑 `--self-test` 现输出
   「**15 条规则**各含红/绿样例」——区间与计数已是两套口径。S15 落地 PR 必须同 PR 同步 §2 该处，否则**新层自己制造一次权威双标**（本单之病的当场复发）。
3. 附带约束（供排期）：`AGENTS.md` 用掉 S6 预算 **74/80 行（92.5%）**、`execution-contract.md` 用掉
   **194/210 行 + 23,995/24,500 字节（97.9%）** → 「就地写清」若落在执行契约正文，**放不下**（仅剩 ~505 字节）；
   其附录现 62/200 行、5,780/20,000 字节（29%）→ 细则该走 v1.12（#1238）已开好的「正文收语义、附录承细则」这条路。

---

## 4. 对 N1–N6 的表态（按 issue 要求优先表态）

- **N1 —— 采纳，并补两条 N1 自己没定的限制。**
  「Ownership Authority only」是对的。本仓最强前例不是 `#724`，而是 **#527 的收口形态本身**：
  `notes/architecture/2026-08-29-log-observation-authority.md:8` 的做法是「**保留双链 + 各限定用途**」，
  并把消费方矩阵写进**被指向的域内文档**（设计文档 §5），而不是写进一个上级索引。
  另两条支撑：`ADR-0011:32`（上位/下位，不构成平行权威源）、`execution-contract.md §3.5`（第二份权威不得合入）。
  **本稿补的限制**：① 索引不挂内容条款后，「≥2 owner」的红由 owner 人工裁决（N1 完整形态措辞可用），但**裁决结果必须写成
  「限定语」而非「判决句」**——#527/#906/#1237 三次裁决都是「同一时刻只有一个权威 *面*」的写法，判决句（「以本文为准」）
  才是复利来源；② 索引行**不得指向留档面**（`docs/notes`、`docs/reviews`）：#2042 明确两者是按需留档、窗口内失效链接都落在两树下，
  指向它们的 owner 行会在下一次裁决后变成假绿。X3 的教训（§2）就是规范面 0 命中、口径全在 notes。
- **N2 —— 部分采纳（1 条真边，其余为伪需求）。**
  「边无主」的诊断对 X3 成立，但**候选三条边里两条不该收**：
  `ScriptVersion ─bound_to→ PlanStep` 在代码里是**刻意无 FK**的软引用（`models/plan.py:101-102` 只有
  `script_name`/`script_version` 两列，无 `ForeignKey`），其完整性归属已被 `is_active` 过滤
  （`plan_dispatcher_sync.py:261`、`precheck/scripts.py:27`）+ 不可变门禁（`check-script-version-immutability.py:53`）
  三方占住，登记它**不改变任何未来决策**→ 伪需求。`工具实现 ─executed_by→ 宿主` 已被 `ADR-0033` 三层宿主 +
  `ADR-0027:118-130` 分别占有 → 也不缺 owner。**只收 `Merge ─executed_on→ 控制面本机` 这一条**，
  因为它的三种说法（0025 域 / 0025+0033 域 / B1 归 0033）确实是同一条边的不同裁决，且现态真源单一（`dedup_scan.py:905`）。
- **N3 —— 采纳白名单，且指出白名单已有现成实现形态**（`LIVE_SURFACES` 式表驱动 + 假阴性显式声明，§3.5）。
  对「退化成只检查自己写过的东西」的回答见 §3.4：抄 `ratchet:20-22` 的「扫不到即红」。
  但**反对**把白名单当作唯一判据域：白名单 + 无「新增声明必须入表」的配对 = 表永远绿而冲突在表外生长（这正是 X3 今天的状态）。
- **N4 —— 采纳，并给一个比 N4 更强的可执行替代。**
  「Referential ≠ Semantic」正确；但结论不必是「语义一致性只能人工」。实测：把 11 条 S11 锚的**原文子串**拿去扫 48 篇 ADR，
  命中面极小——只有 `pipeline_def.lifecycle`(5 处/2 篇)、`Redis 只承载队列`(1/1)、`secure cookie、受限 SameSite 和 CSRF guard`(1/1)、
  `不可原地修改`(3/2)、`frontend/src/utils/api/types.ts`(4/2)，其余 6 条锚 0 命中。
  → 判据「**Accepted ADR 的 diff 复述了受治不变量原文子串 → 同 PR 必须改 `AGENTS.md`/锚表，或带理由豁免**」的
  FP 面 = **14 处 / 8 篇**（48 篇中的 0020、0022、0023、0024、0036、0039、0045、0046）、且**当场能命中本案**（`ADR-0039:16` 逐字引用了 `AGENTS.md:15` 那句，是全仓 7 处命中之一，见 §9 命令）。
  这把 F5 的「不可实现」变成「**字面共存 + 同 PR 义务**」——机读、不新增内容、不造第 5 权威。
  再补一条零成本动作：`check-script-version-immutability.py:2`/`:194` 现在只写「ADR-0020」无 `vX.Y`，
  而 S14 只绑「紧跟 ADR 号的第一个 `vX.Y`」→ **在该注释补一个版本 token，F-1 窗口立刻从『无人可查』变成『S14 当场可查』**（ADR-0039 落地 PR 顺手可做）。
- **N5 —— 采纳方向，但禁止其字面形态。**
  「ADR identity 不可变」我完全支持（F-3 结论成立）。但**「处置档位」不得成为状态词表的新成员**：
  `_ADR_STATUSES = {"Proposed","Accepted","Superseded","Deprecated"}`（`check_governance_surface.py:301`）
  是封闭词表，头部状态行正则 `:302-304`、缺行/不可解析即红 `:567-586`、`adr/README` 状态 cell 同样封闭 `:594-608`（#1524/#2035 两次事故换来的）。
  引入 `Scoped`/`Historical` = 改 3 处判据 + 全索引面回填 + 触发 #2035 型「静默退出校验」风险。
  **仓库现例已经给了不带新状态的写法**：`ADR-0033:171`（§5.3，「D2 的语义本身不变，变的只是**适用范围**」，状态仍 Accepted）。
  → N5 应写成「**版本记录 + 适用范围句**三档（Superseded/收窄/历史化）」，其中只有 `Superseded` 是状态 token。
- **N6 —— 采纳五层定义，反对「写入文档头部」的落点。**
  五层里 **Domain Authority** 这一层确实必须有（否则 N1 的「内容权威留在域内文档」没有落点）。
  但把它写进**新建文档头部**恰好触发 N1 要防的事。既有更合适的宿主：`docs/DOC-MAP.md:115-117`
  「## 权威 vs 归档」已经是「哪些面是权威 / 哪些是归档」的分层声明（且该节现仅 4 行，扩容无预算压力）→
  **一行**即可把五层词汇钉住，不需要第 5 份文档。

---

## 5. F1–F7 逐条

- **F1（立项必要性，要量化判据）**：**部分成立**。判据我给三条可算的：
  ① *真冲突数 / 声明数*：30 命中里真 owner 断言 ≈12，其中**互相争夺同一概念同一面的 = 0**（§3.1、§2）；
  ② *复发率*：30 天内 `docs/notes` 有 8 篇触碰「权威分家/边界/归属/平行权威/双标」（同期唯一 note 文件 960 篇，≈0.83%），
  其中裁决型 4 篇（08-29、09-04、09-08、09-11）≈ **每周 1 次** → 病在复发，但每次的解法都是「限定语 + 回指」，
  成本 1–2 行，**从未**因为「没有 owner 表」而解不掉；
  ③ *必要阈值（建议采信）*：**只有当「同一概念同一面的归属被两个不同 PR 先后改写」出现 ≥2 例**才值得建独立文档；
  本单三处都不是这一型。→ 结论：**建层（覆盖图加列 + 一条判据），不建文档**。
- **F2（会不会长成第 5 份权威 / 边界与 supersede）**：**会**——只要它自称内容权威（二.2 已判定，采纳 N1）。
  补充一个 issue 未量的生长机制：`§N1` 的归属裁决若写成判决句，每次冲突都要在索引里补一段解释，
  而解释一旦存在就必然与被指向文档漂移（本稿实测：30 小时内 DOC-MAP 两行内容未改而行号 106→108；
  `#2663` 的「17/17 已全覆盖」一天内变谎话）。**边界规则建议钉成一句**：owner 表只允许出现
  「概念/关系 + owner 文档+节 + 代码真源 + 派生面 + 复议触发器」**六列且不得有第七列承载解释**；
  解释写在 owner 文档里。supersede 方向唯一：**表 → 被指向文档（内容）**，反向由 §3.5 的既有纪律处理。
- **F3（假阳性面 + 分层 vs 真冲突）**：见 §3.1/§3.2。补一条判定规则，使「分层权威」不再需要人工分辨：
  **owner 表按「概念 × 面（facet）」两键定行，不按概念定行**。X1 三腿、X2 两链、#527 双链、#906 四段化（当前状态 vs 目标形态）
  全都是「同概念不同面」。有 facet 键则「≥2 owner」的判定域缩小为「同一 facet 两行」——这才机读；
  没有 facet 键，S15 与 `ADR-0029:301`+`:390` 这种同篇两书都会立刻假阳（§3.1 末）。代价：facet 列需先给 8–12 行各挑一个词，
  约 1 小时人工，且**必须禁止 facet 自由文本化**（建议给受控词表：`身份 | 内容 | 运行时可选面 | 存储布局 | 生命周期 | 展示口径 | 宿主/执行位置`）。
- **F4（三处收口口径）**：见 §2 表第 4 列——每处 1–2 行，X3 的拆边与 B0 相容（B0 只裁时点，不裁归属，`backlog:32`）。
  额外一条：X1 第三腿的正确处理是**删归属、不是判 owner**（0020 无此断言）。
- **F5（F-1 窗口时序）**：**不等 0039**，但也不是「加一条语义一致校验」（issue 三.5 的论证我复现且同意：无机械判据）。
  本稿的可执行替代 = 三件小动作：① 给 `AGENTS.md:15-16` **挂 S11 锚**（纠-1：它现在根本不在锚表内，
  比 issue 描述的更盲）；② 采用 N4 的「字面共存 + 同 PR 义务」判据（FP 面 ≤6 篇，当场命中 `ADR-0039:16`）；
  ③ 给 `check-script-version-immutability.py` 的 ADR 引用补 `vX.Y`，让**既有** S14 成为该机读链。
  ② 与 ③ 都是改判据输入，不是新权威源。指针本身：`AGENTS.md` 只剩 6 行预算（§3.6-3），若要加指针，
  **优先加在被指向 ADR 侧而不是 AGENTS 侧**。
- **F6（多 Harness 适配 / 48 篇补齐）**：**「必填字段」不会生效**。实测既有模板字段合规率：
  `状态 48/48`（**唯一被 S12 强制的字段**）· `决策者 45/48` · `标签 45/48` · `日期 46/48` · `优先级 38/48` · `目标里程碑 37/48`。
  → 只有进门禁的字段能到 100%，「模板必填」的经验上限是 96%、软必填是 79%。
  若 S15 判「每篇 ADR 必须有 `归属域`」→ 首日 48 篇全红 → 只能 grandfather 全存量（等于没有判据）或一次性 48 文件改动
  （撞「不做-2 不预填」与共享元文件串行）。**建议形态**：不设 ADR 字段，改为「**新 Accepted 的 ADR**，
  若其决策涉及表内任一概念/关系，必须在同一 PR 内动表」——即 N4 的同 PR 因果链，天然只约束增量、无需补齐。
  与 ADR-0034 的衔接：本表所在文件的修改属**共享元文件**，沿用「同一时间只由一个 Execution 修改」，不新加规则（同意 issue 三.6）。
- **F7（可测性）**：**「出现新双标时 S15 必红」不可实现**（§3.2：X3 三处连「权威」二字都没有）。
  本稿给的三条**可实现且非自反**的验收判据：
  ① 表内每行的代码真源可解析（`SourceGuard.anchored(…, expect=1)` 式，0/≥2 命中都红）；
  ② 规范面（`docs/{design,adr,development,operations}` + `AGENTS.md`）内**新增**「`唯一.*权威`」句式必须命中表内一行或带理由豁免
  （只看 diff 新增行——照 `check_invariant_diff.py` 的「存量不误伤 + BLOCK + 宁缺勿误报」口径，`invariant-diff` 已在 `check:pr`）；
  ③ **非零命中自检**：判据在 main 上必须能扫到 ≥1 处已知存量（`ratchet:20-22`），上线基线 = §2 的 X1 三处锚。
  「三处双标收敛为一行」应作**人工验收**（本稿 §2 第 4 列就是那一行的候选文本），不作门禁断言。

---

## 6. 对 A / B / C 表态，与本稿推荐的 A′

### A（新文档 + 模板必填字段 + S15）—— **Needs-revision**

方向（登记归属 + 机械校验）成立，但**三条机制各自失败**：① 宿主选错（新文档 = 第 5 权威的最大风险源，且与 OPEN 的
`#724`「标准化领域分类与职责范围总览」职责重叠——本稿新增：#724 至今未落成任何文档，`git grep -i "track index"` 0 命中，
说明「归属目录」这件事在本仓的既有落点是**看板卡片**，不是 design 文档）；② 必填字段不生效（F6 实测 79–96% 上限）；
③ S15 的判据面既漏 X3 又当场假阳（§3.1/§3.2）。
按 issue 自己的分级定义（「阻断 = 须改再落」），A 带着 §7 的 C-01/C-02/C-03 三条阻断就不可能是 Accept-with-nits——
**这是本稿与既有两稿的唯一总评分歧，且我认为是口径问题而非事实分歧。**

### B（Core Semantics → Invariants → Domain ADR + 全量 Inventory）—— **Needs-revision**

同意 issue 的倾向，并给一条它没被提到的**正面重复证据**：全量 inventory 与既有 `§7.1 强制力覆盖图`（10 行 × 4 列，
`docs/design/2026-08-governance-surface-protection.md:134+`）**同形**，与 `docs/adr/README.md` 主表（S12 的派生索引面）
与 `docs/DOC-MAP.md:43-114` 分层登记面**三处正面重复**。B 的「五问决策树」落点确实应独立立项：
`declare` 的在窗查重已机械化（`§3.4` + `ai_work.py:756-766`，本稿一手 `--force` 证据），
再加五问属**改变所有 harness 领单动作** = ADR-0034 域（同意 issue 四）。

### C（不立项，靠 #530 式逐对收口）—— **Accept-with-nits（方向）**

本稿证据支持 C 的核心：**三处双标都能就地解决，且本仓历史上 4 次同类裁决都是就地解决的**。
但 C 原写法「维持现状」有一个可测量的洞：**没有任何规则要求「新概念出现竞争性权威断言时入覆盖图」**——
`§7.1` 的维护靠的是人自觉（其 `check_invariant_diff.py:15` 只写「扩展时机 = 新不变量入覆盖图时同步加模式」）。
所以 C 必须补两条（= 下面的 A′）才不依赖自觉。

### A′（本稿推荐，成本低于 A，且不改任何 ADR 正文）

1. **宿主**：不新建文档。在 `docs/design/2026-08-governance-surface-protection.md` §7.1 旁增
   **「概念/关系 → owner(文档+节) → 代码真源 → 派生面 → 复议触发器」表**（复用同文件既有 4 列形 + 已存在的维护棘轮句）；
   若 owner 坚持新建文件，则**必须**：不挂内容裁决条款（N1 完整形态）+ 加入 `RESIDENT_BUDGETS` 时**同 PR 建文件**（§3.6-1）
   + 同 PR 同步 `:29` 的「S1–S14」区间（§3.6-2）。
2. **首批 8–12 行**：采纳 facet 两键（F3），必含 1 条边（`Merge ─executed_on→ 控制面本机`，N2），
   收 §2 的 X1 三 facet + X2 两行 + `Execution` + `Tool vs Script vs Adapter` + `中心存储路径`；
   显式声明「`docs/notes`/`docs/reviews` 不作为 owner 指向目标」。
3. **判据**：一条对拍测试 `tests/test_semantic_ownership_claims_are_live.py`（复用 `tools/dev/source_anchor.py`），
   三条断言 = F7 的 ①②③；**不动** `check_governance_surface.py`，不加 GATES key，不触 S5x。
4. **X1/X2/X3 就地收口**：按 §2 第 4 列，共约 6 行文档改动 + `check-script-version-immutability.py` 注释补 `vX.Y`（N4-③）。
5. **AGENTS.md**：给 `:15-16` 挂 S11 锚（纠-1）。仅此一条，不加常驻入口文本。

---

## 7. 发现清单（稳定 ID；级别 = 阻断/建议/观察）

| ID | 级别 | 发现 | 证据（`ee75d173`） | 落点 | 与既有稿 |
|---|---|---|---|---|---|
| **C-01** | 阻断（对 A ①） | 新表不得挂「冲突时以本文为准」；只拥有 Ownership Authority | `2026-storage-roles-and-aliases.md:4` 是**域内宪法**式内容条款；`execution-contract.md §3.5`(:107-119) 禁止第二权威位 | 采纳 N1 完整形态 + 本稿两条限制（§4-N1） | 收敛（采纳 N1） |
| **C-02** | 阻断（对 A ③） | 「`file:line` 不可解析→红，复用 S2」三重不成立；且裸行号半衰期以天计 | `check_governance_surface.py:147-164`、`:143-146`、`:157`；DOC-MAP 106→108 而正文未改 | 改用 `tools/dev/source_anchor.py:111-117/173-179`；行号仅人读 | 收敛 + **本稿新增**：S2 剥行内 code / 丢 `#` fragment；501 commit/30h 的漂移率 |
| **C-03** | 阻断（对 A ③ / F7） | 声明面门禁**扫不到 X3**（0/0/0），且「≥2 owner」上线即假阳（同篇两句 3 组） | §2 表 + §3.1 分类计数（30/4/4/~10/≈12） | 判据换成「diff 新增行 + 表内命中 + 非零命中自检」 | **本稿新增**（含 X3 漏检与 FP 计数） |
| **C-04** | 建议 | 宿主应为既有 §7.1 覆盖图，不新建文档（同时避开与 OPEN `#724` 的职责重叠） | `2026-08-governance-surface-protection.md:134+`（10 行×4 列，含 1 条总原则行）；`git grep -i "track index"` = 0 | A′-1 | **本稿新增** |
| **C-05** | 建议 | A ② 必填字段不会生效；改为「新 Accepted + 涉及表内概念 → 同 PR 动表」 | 字段合规率 48/48（唯一被 S12 强制）vs 37–46/48（未强制） | A′-1 / F6 | **本稿新增**（数字为本稿实测） |
| **C-06** | 建议 | F-1 的正确修法是先挂锚：该句根本不在 S11 覆盖内；S14 也可被「补 `vX.Y`」激活 | `AGENTS.md:15-16`（`## 总原则`@6 / `## 硬不变量`@23）、`check_governance_surface.py:268-283`、`check-script-version-immutability.py:2`/`:53`/`:194`、S14 `:738-786` | F5 三动作 | **本稿新增**（S14 激活 + 锚 FP 面 = 14 处/8 篇实测） |
| **C-07** | 建议 | 「膨胀上界用 S6」不成立；预算 key 无存在性保护 → 先加 key 会崩 `--check` | `:202-224`（13 key，无 `docs/design`）、`:1280-1286` 对比 `:1276` | 行数上界由新判据自卡（≤N 行） | **本稿新增** |
| **C-08** | 建议 | 任何新规则必须自带扫描口径，且同步 `S1–S14` 写死区间 | `2026-08-governance-surface-protection.md:29`；`notes/bug-fix/2026-09-18-gov-doc-drift-2659.md:70`；`--self-test` 实跑「15 条规则」；`.wt/` 污染 1943/1670 | 落地清单 | **本稿新增** |
| **C-09** | 建议 | 判据落 `tests/` 而非 `tools/dev/`：后者会绕开刚建立的 #2639 锚点棘轮 | `tests/test_source_scan_anchor_ratchet.py:30`（SCAN_DIRS 不含 tools）；`ci.yml:130` | A′-3 | **本稿新增** |
| **C-10** | 观察 | `Scoped`/`Historical` 不得成为 ADR 状态 token；写「版本记录 + 适用范围」 | `check_governance_surface.py:301-304`、`:567-586`、`:594-608`；现例 `ADR-0033:55` | §4-N5 | **本稿新增** |
| **C-11** | 观察 | X2 的聚合面已存在、缺的是回指；「四层」实为 2 断言 + 2 描述 | §1.2 纠-3 全串 file:line | A′-4 | **本稿新增** |
| **C-12** | 观察 | 问题定性「无任何机械保证」不准确：§3.5 + `ai_work.py:55/536/756-766` 已是机械点 | 本稿 declare 被 `[REFUSED]` 的一手输出 | 改写定性句 | **本稿新增** |
| **C-13** | 观察 | 表内行的半衰期：Y1(#2631) 从发现到收口 <24h，修法即「代码回指既有权威定义 + 注释引 ADR-0029 D6」 | `results.py:40/245-262`、合入 `492a1bf5`，实现 commit `84193d63` 同时补 `+251` 行后端测试 | 真值在代码/查询里的行 → 落对拍测试，不落文本表 | **本稿新增** |
| **C-14** | 观察 | 落盘口径类数字（45/47/48 篇、232/235 行、:107/:106/:108）一律带 commit + 口径；F-2/F-4/F-5/F-6 可直接引用 | §1 全表 | issue 文书 nit | 收敛（issue 已自提「数字须自带口径」） |

---

## 8. 结论分级汇总

- **阻断（须改再落）**：C-01（索引不得挂内容裁决条款）、C-02（`file:line` 不得作为判据 / 不得复用 S2）、
  C-03（F7 判据漏 X3 且当场假阳）。
- **建议**：C-04（宿主改 §7.1 覆盖图）、C-05（必填字段改「同 PR 动表」）、C-06（先挂锚 + 补 `vX.Y` 激活 S14）、
  C-07（预算 key 存在性 + 行数自卡）、C-08（口径与 `S1–S14` 区间同步）、C-09（落 `tests/`）。
- **观察**：C-10…C-14。
- **总评**：A **Needs-revision** · B **Needs-revision** · C **Accept-with-nits** · 推荐落点 **A′（= C 的方向 + 覆盖图加列 + 一条非自反对拍判据）**。

**留给 owner 裁决的三问（本稿不代答）**：① 宿主是覆盖图还是新文件（若选新文件，能否接受「与 #724 分工」必须先裁）；
② facet 受控词表由谁定词（本稿给了 7 个候选，§F3）；③ F-1 的三件小动作是否单独开实现单（本稿认为 ①②③ 里只有
「给 `AGENTS.md:15-16` 挂锚」需要单，另两件可在 ADR-0039 落地 PR 顺带）。

---

## 9. 复现命令（本稿全部数字来源）

```bash
# 基线
git rev-parse HEAD; git rev-list --count cebde359..HEAD            # 501
ls docs/adr/ADR-*.md | wc -l                                        # 48
grep -c 权威 docs/adr/ADR-*.md | sort -t: -k2 -rn | head -8         # F-2

# F-3 多口径（唯一可信：git grep tracked@HEAD）
for n in 0020 0025 0028 0033; do
  printf "%s files=%s no_notes=%s code_ext=%s\n" $n \
    "$(git grep -l ADR-$n | wc -l)" \
    "$(git grep -l ADR-$n -- ':!docs/notes' | wc -l)" \
    "$(git grep -l ADR-$n -- '*.py' '*.ts' '*.tsx' '*.sql' '*.sh' | wc -l)"
done
cd /home/debian13/stability-test-platform && grep -rl ADR-0020 . | wc -l          # 1943（.wt 污染）
grep -rl ADR-0020 . | grep -c '^\./\.wt/'                                          # 1670

# F-4/F-5
find backend/agent/scripts -maxdepth 1 -type d | tail -n +2 | wc -l                # 34
find backend/agent/scripts -maxdepth 2 -type d -name 'v*' | wc -l                  # 179
find backend/agent/scripts -name '*.py' | wc -l                                     # 321
find backend/agent/scripts -name '*.py' -print0 | xargs -0 wc -l | tail -1          # 107464
ls -d backend/agent/scripts/flash_*/v* | wc -l                                      # 27（flash_firmware 单族 22）
grep -ci '刷机\|flash' docs/adr/ADR-0033-*.md                                        # 0

# 行号漂移 / 锚文本唯一性
git show cebde359:docs/DOC-MAP.md | grep -n 'AI Execution Contract 唯一权威源'      # :106
grep -n 'AI Execution Contract 唯一权威源' docs/DOC-MAP.md                            # :108，计数=1

# 声明面 FP
git grep -nE '唯一权威|权威源|single source of truth|以本文为准' -- '*.md' \
  ':!docs/notes' ':!docs/reviews' ':!docs/archive' | wc -l                           # 30
# X3 不含「权威」（本稿 C-03）
sed -n '11,12p' docs/notes/architecture/2026-09-15-center-storage-and-merge-locus-proposal.md | grep -c 权威   # 0
sed -n '247p'   docs/notes/process/2026-09-15-device-log-flow-issue-backlog.md        | grep -c 权威            # 0
sed -n '130p'   docs/adr/ADR-0027-*.md                                                | grep -c 权威            # 0
# 归属句式只存在于留档面
git grep -nE '(属|归|属于).{0,12}ADR-[0-9]{4}.{0,14}域' -- docs/design docs/adr docs/development docs/operations AGENTS.md   # 0

# X1 代码真源
grep -n 'content_sha256' backend/models/script.py                                     # :19
sed -n '12,37p' backend/services/precheck/scripts.py                                  # plan_snapshot ∩ Script 表
grep -n 'script_meta\|唯一' docs/adr/ADR-0020-*.md                                     # script_meta 0 命中

# S2 对 file:line 的三种行为（本稿一次性探针；结论见 §3.3 表）
python3 - <<'P'   # 造两份探针：A 尾随 #L 假绿、B 冒号形态假红
open('docs/reviews/.pA.md','w').write('# t\n\nsee ' + '[x](../../AGENTS.md' + '#L9999)\n')
open('docs/reviews/.pB.md','w').write('# t\n\nsee ' + '[x](../../AGENTS.md' + ':9999)\n')
P
python tools/dev/check_governance_surface.py --check | grep '\.p[AB]\.md'   # 只有 pB 出现
rm docs/reviews/.pA.md docs/reviews/.pB.md

# 门禁形态 / 预算
.venv/bin/python tools/dev/check_governance_surface.py --self-test                     # 「15 条规则」
grep -n 'RESIDENT_BUDGETS = ' -A 24 tools/dev/check_governance_surface.py              # 无 docs/design
sed -n '1280,1286p' tools/dev/check_governance_surface.py                              # 无存在性保护
awk 'END{print NR}' AGENTS.md                                                          # 74 / 预算 80
wc -l -c docs/development/ai/execution-contract.md                                     # 194 / 23995 → 194/24500B 97.9%
git grep -n '不可原地修改' -- docs/adr                                                 # 2 篇 / 3 处（含 ADR-0039:16 逐字引用）
```

**未验证 / pending**（按「命令成功不等于验证通过」如实标注）：

- 未实现 S15/对拍测试原型，故 §3.4 的「非零命中」与 F7③ 的判据形状是**设计断言**而非实测通过；
- 未跑 `check:quick` 全集来验证「新表文件是否触发其它门禁」——但本稿 §3.6 的两处陷阱来自读码，属确定事实；
- 未审 `docs/DOC-RETIREMENT.md` 的归档面规则是否与 owner 表宿主冲突（潜在交叉点，交 synthesis）；
- Y2（#2629 审计筛选词表漂移）未复核前端选项集，只确认后端 `schemas/audit.py:13-14` 为裸 `str`。
