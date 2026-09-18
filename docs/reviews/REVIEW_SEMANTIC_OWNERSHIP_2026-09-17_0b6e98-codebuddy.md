# 跨 ADR 语义所有权层立项 独立评审（CodeBuddy）

- **评审对象**：issue #2546 —— 治理层立项判断 + X1–X3 三处权威双标 + 候选方案 A/B/C（执行契约 Mode C 独立评审）
- **评审方**：CodeBuddy（会话 `01a0b380-dec4-7654-b249-b24dc20b6e98`，harness 归属：CodeBuddy CLI）
- **评审日期**：2026-09-18
- **基线**：`origin/main = ee75d173`（自记 commit；事实采集起于 `497952c8`，两 commit 间差异仅 `docs/reviews/` 新增一份他稿，下表数字在 `ee75d173` 复核未变）
- **独立性**：本稿未阅读 `004377` / `ae8cd9-cursor` 等既有稿正文、未引用其结论；终检阶段的机械 grep 曾输出他稿个别行，凡与本文结论重合处均已由本文自测命令独立复验（§1.1 与 §2.1 的代码真源为本文自测）。
- **只读边界**：未修改任何 ADR / 代码 / 测试 / issue 结论区；测试用 `文件存在性/字符串` 级检查，未触库、未触生产控制面。

## 0. 总评

| 方案 | 表态 | 一句话 |
|---|---|---|
| A（推荐稿：Living 表 + ADR 头字段 + S15） | **Accept-with-nits** | 方向采纳；四处修订：冲突条款必须反转（F2）、删 A② 的 ADR 必填字段改「表→ADR 反查」、S15 缩为两条可机读判据、膨胀上界不用 S6（F3/F6） |
| B（三层 Core Semantics + 全量 Inventory） | **Needs-revision** | 不采纳主线；只并入「一个概念恰好一个 owner」为**表内写入规则**（非门禁），五问压成一条 declare 提示 |
| C（不立项，逐对收口） | **观察** | 不阻断；但 14 天内 ≥2 例新双标 + 1 例门禁无感的窗口（F-1）说明逐对收口未收敛；若 A′ 落地则不必选 C |

**立项判断：建议立项，条件是「有机械挂钩」。** 纯文档层的 owner 表会腐化（DOC-MAP 自称含「权威归属」却只有类别级，`docs/DOC-MAP.md:7,115-121`）；仅当 S15 至少有一条真能拦合并的判据时，正收益才成立（F1/F3）。

---

## 1. 事实复测（F-0–F-6）

| 项 | issue 值 | 本稿复测值（`ee75d173`） | 判定 |
|---|---|---|---|
| **F-0** ADR-0039 域归属 | Script 域；「日志链/log chain」0 命中 | 标题「脚本版本不可变契约收窄——直至零引用退役」（`ADR-0039:1`），Proposed v0.1；`日志链/日志事件/log chain/log_chain` 全篇 0 命中 | ✅ 一致 |
| **F-1** 窗口 | AGENTS.md「硬不变量」与 D1 冲突；S11 不校验语义一致 | 见 §1.1 纠偏 1：**该行不在「硬不变量」节、S11 零锚点覆盖** | ⚠️ 比 issue 描述更宽 |
| **F-2** 权威密度 | 0033=13、0026=7、0018=6、0034=6、0021=4、0029=3、0028=1；密度最高=脚本/工具域 | `grep -c 权威 docs/adr/ADR-*.md` 逐值一致（13/7/6/6/4/3/1） | ✅ 一致；ADR 主档 **47 篇**（+1 附录，issue 基线称 45，已增长） |
| **F-3** ADR 引用面 | 0020=132、0025=129、0028=71、0033=25 文件 | 口径依赖：`git grep -l`（tracked，排除自身文件）→ 0020=**169**、0025=114、0028=73、0033=54；含 `docs/adr/` 目录 → 186/125/75/58 | ✅ 结论一致（量级=代码侧稳定外键）；绝对值因口径不同，请勿混用 |
| **F-4** 脚本规模 | 34 族 / 179 版本目录 / 321 py / 107,464 行；flash_firmware=22 | **逐值完全一致**；flash_preflight=5（合 27）；`unisoc_probe`/`unisoc_signal_trigger` 在仓 | ✅ 一致 |
| **F-5** 分类学空洞 | ADR-0033「刷机/flash」0 命中 | 0 命中；flash 两族 27 个版本目录 | ✅ 一致 |
| **F-6** 命名坐标 | Tier 1/2/3、T0–T3、P0/P1/P2、M1–M7 | 四套均在：`ADR-0033:91-93`、`ADR-0031-A:17`、`docs/adr/README.md:14-16`、`docs/adr/README.md:31` | ✅ 一致 |

### 1.1 复测中发现的两处纠偏（对本单论据有增益）

**纠偏 1（F-1 的暴露面比 issue 写的更大）**：`AGENTS.md:15` 那句「已发布 `backend/agent/scripts/<name>/v<version>/` 不可原地修改或删除」位于 `## 总原则`（`:6` 起），**不在** `## 硬不变量`（`:23` 起）——issue 正文归错节。更关键的是：S11 锚表 11 条（`tools/dev/check_governance_surface.py:270-282`）**不含该句**，全仓对该措辞的机械引用只有 `AGENTS.md:15` 自身与 ADR-0039/DOC 的转述（`git grep '不可原地修改或删除'`）。
→ 所以当前暴露不是「锚与 ADR 语义不校验」，而是**零锚点覆盖**：今天改写或删除该行，门禁前后**完全无差别**。真正拦行为的是 `tools/dev/check-script-version-immutability.py`（CI 对比 base 的独立门禁），ADR-0039 的落地清单已列其修订（`ADR-0039:158`），但**没列 `AGENTS.md:15`**。

**纠偏 2（X1 的第三条腿是已漂移的机制描述，不是第三个权威）**：`ADR-0021:96` D4 的对账机制写「后端用 `plan_snapshot.script_meta[*].content_sha256` 对账」，但当前实现中：
- 派发快照构造 `build_plan_snapshot`（`backend/services/plan_dispatcher_core.py:525-575`）的 steps 键为 `script_name/script_version/nfs_path/param_schema/default_params/params/…`——**无 `script_meta`、无 `content_sha256`**；
- 准入期期望 sha 取自 **`script` 表**：`backend/services/precheck/scripts.py:24`（select `Script.content_sha256`）、`:36`（`"sha256": r.content_sha256`）。
→ `plan_snapshot.script_meta` 是**幽灵字段**：该表述在同一 ADR 内已被部分删除（`:174` 带删除线），但 `:45`、`:99`、`:297` 尚存。X1 的收口因此不只是「加限定词」，还要把 0021 的机制描述对齐代码。

---

## 2. X1–X3 单行裁决（对应 F4）

### 2.1 X1 脚本「唯一权威」三口径

**裁决**：按**面**拆分 owner，不是三方对立——
- `Script 内容（sha 真值/写入路径）` → **ADR-0021 D4**（`:96`）；对账字段改为 `script.content_sha256`（代码真源 `backend/services/precheck/scripts.py:24,36`）。
- `Script 运行时版本解析（调用标识/版本身份）` → **ADR-0033 D3**（`:121`）；其「唯一运行时权威」保留，建议补限定词「运行时**版本解析**」。
- `plan_snapshot` → **ADR-0020**，是**派发冻结面**（承载 `(name,version)`/`nfs_path`/参数），不是 sha 权威；`ADR-0021:297` 的「`plan_snapshot.script_meta` 作为权威」改为「作为派发冻结依据」。
- 证据：`ADR-0021:96,99,174,297`；`ADR-0033:121,164`；`backend/services/plan_dispatcher_core.py:525-575`；`backend/services/precheck/scripts.py:24,36`。

### 2.2 X2 日志域四层

**裁决**：分层自洽 ⇏ 双标——这是 F3 假阳性面的**教科书样例**，处理方式是**只加汇总行、不重写任何一层**：
- owner 表登记 4 行：`DLE=ADR-0028:44` / `job_log_signal=ADR-0018:315` / `行为·分区·merge=ADR-0032:55-71` / `存储模型=ADR-0025`；
- 「没有任何一处汇总」成立但已有半成品可挂：`docs/design/2026-adr-0025-log-flow-sequence.md:283` §5 消费方矩阵（signal×DLE 两层）+ `docs/notes/architecture/2026-08-29-log-observation-authority.md`（边界决策记录）。表行直接引用它们，**不要造第二份矩阵**。
- 附带发现（支持本层价值）：实体名漂移——`ADR-0018:315` 写 `log_signal`，实际表名 `job_log_signal`（`backend/models/job.py:175`）；表行应统一用真名并登记别称。

### 2.3 X3 merge 执行位置归属

**裁决**（一行）：`merge 执行位置（I-13）归属 ADR-0025（存储/部署形态面）；B 段（B1 迁 worker/Agent）是对 ADR-0033 工具宿主模型的**依赖**，不是归属；实例绑定限制登记面=ADR-0027 v1.8 第 7 条。`
- 与三处文本相容：提案「两项均属 ADR-0025 域，不新立 ADR」（`2026-09-15-center-storage-and-merge-locus-proposal.md:11-12`）对 I-12 精确、对 I-13 是**粗粒度**表述；backlog「ADR-0025 / ADR-0033 域」（`2026-09-15-device-log-flow-issue-backlog.md:247`）把依赖写成了并列——建议改为「归属 ADR-0025；B 段依赖 ADR-0033」；`ADR-0027:118-131` 仅对 B1 说「归 ADR-0033」，与依赖关系一致。
- 与 B0（2026-09-16，不提前 B）相容：B0 只裁时序，不改归属（同文件 `:75`、`ADR-0027:168`）。
- 行为面权威另属 ADR-0032（`ADR-0033:36-44` 权威分家表：行为=0032、结构/宿主=0033），表行需同时登记这一层，避免新的「归 0025 还是 0032」追问。

---

## 3. F1–F7 逐条

### F1 立项必要性 —— **建议**（支持立项，附可证伪判据）

- 量化：近 14 天新增 ≥2 例（X3 2026-09-15、F-1 2026-09-17 识别）+ 存量 2 例（X1/X2 口径漂移）；其中 X2 的同类问题已在 #527/#530 手工收口过一次（矩阵现状见 §2.2），仍未阻止 X3 新发生——**逐对收口的产出速率低于复发速率**。
- 但「有表就赢」不成立：见 F2。判据（可证伪）：A′ 落地后 30 天内，是否至少 1 次在合并前拦下新双标（含 F-1 类窗口）；拦不下即降级为人工清单（承认 C 更优）。
- issue 问「ADR-0011:32 那种就地写清是否够」：够用于**单点**，但 F-1 证明「就地写清」的前提（有人同时看两处）不成立——本单三处双标无一来自新决策，全部是旧文本随时间漂移。

### F2 方案 A 的表会不会长成第 5 份平行权威 —— **阻断**（对「照抄 storage-roles 冲突条款」这一形态）

- 会。`docs/design/2026-storage-roles-and-aliases.md:1-6` 的「冲突时以本文 + 代码为准」成立，是因为它**拥有内容**（角色定义）；owner 表只拥有**指针**。指针表声明「冲突以本文为准」= 用 Living 文档覆盖 Accepted ADR 的自述，治理上是倒挂。
- 修订：冲突条款**反转**——「本表只登记归属，不定义语义；概念语义以 owner 文档为准；本表与 owner 自述冲突 → 触发『复议触发器』列人工裁决，禁止静默以表覆盖 ADR」。
- 边界（谁 supersede 谁）：谁都不 supersede。`DOC-MAP.md` 保留**类别级**分层与登记（`:115-121`），本表做**概念级**；storage-roles 继续拥有存储角色（本表相关行只放指针）；`execution-contract.md` 继续是执行语义唯一权威源（本表不得对 Execution 语义写行内定义，只登记归属）；AGENTS.md 硬不变量优先（F-1 的修法见 F5）。表状态建议标 **Living-Navigational**，并在 DOC-MAP「权威 vs 归档」补一行登记。

### F3 S15 判据可机读性 —— **阻断**（对三条判据原样落地）

| 判据 | 可机读性 | 假阳性面 / 处置 |
|---|---|---|
| ① 同一概念 ≥2 owner → 红 | **不可机读** | 「分层权威 vs 真冲突」是语义判断，X2/X3 即字面 ≥2 引用而实际自洽；按字符串实现必假阳，按表内唯一实现则恒真（表自身定义 owner）。→ **降级为人工清单**（评审/ADR 模板勾选项），不做门禁 |
| ② 表内 `file:line` 不可解析 → 红 | **可做**（复用 S2） | 只证存在不证语义；**用「文件存在 + 锚点字符串可匹配」代替行号锚**（行号必烂，S2 现状即文件级）。低假阳、真收益 |
| ③ ADR bump 涉及归属域行 → 表须同 PR 出现 | **可做，但不需要 A②** | 从表反查 ADR 号（owner 单元含 `ADR-NNNN`）即可，无需 47 篇主档回填、无需 diff 上下文；再复用 S12 的版本头解析（`tools/dev/check_governance_surface.py:313`）比对表内 `owner_version`，不一致→红。代价=ADR 任意 bump 都要求动表（可接受，且这正是「发现即同步」） |

- S15 实际形态建议：**两条**（②锚点可解析 + ③owner 版本一致性），带 `--self-test` 正反样例（沿 `check_governance_surface.py:53-64` 的 verify-before-asserting 纪律）。
- 膨胀上界：**不能用 S6**。`RESIDENT_BUDGETS`（`:202-223`）登记的是常驻启动/半常驻契约面（AGENTS/CLAUDE/cursor rules/harness 适配/执行契约），design 文档登记进去会让「常驻预算」信号失真。改用 S15 内常量（如 `MAX_ROWS=12`，扩容须同 PR 改常量并留 Note）。

### F4 三处双标收口 —— **建议**（见 §2 单行裁决，可直接采纳）

- X1：owner 按「内容面/运行时候选面/冻结面」拆三行 + 修正 0021 的幽灵字段（代码真源 `backend/services/precheck/scripts.py:24,36`）。
- X2：登记 4 行 + 指针到既有矩阵（`docs/design/2026-adr-0025-log-flow-sequence.md:283`），不重写。
- X3：归属 0025 / 依赖 0033 / 登记 0027；backlog `:247` 的并列写法改一行即可。

### F5 F-1 窗口的处置时序 —— **阻断**（对「S1x 校验语义一致」）；**建议**（对「随 0039 落地同步 + 补锚」）

- 「锚点与 Accepted ADR 语义一致」**不可机读**（语义等价不可判定）；强行做成红灯会复刻 S14 明确要避免的「红灯但不可修死结」（`tools/dev/check_governance_surface.py:46-47`）。
- 可行替代（两步，均低成本）：
  1. **现在**给 `AGENTS.md:15` 补一条 S11 锚点（当前 11 条不含它，见 §1.1 纠偏 1）——保证将来改这行时至少有一次显式动作；
  2. 把「同步 `AGENTS.md:15`」加进 ADR-0039 的落地清单（其 `:158` 已有门禁修订项，加一行≈0 成本），在 0039 转 Accepted 的同一 PR 完成措辞收窄。
- 若仍要机械挂钩：S11 锚点条目允许可选 `ref: ADR-0039@Proposed`，当该 ADR 状态位变化时**告警**（非红）提示复核——判据确定性成立，但引入常驻状态，建议不做，或仅在 0039 落地前置检查里一次性核对。

### F6 多 Harness 适配 —— **建议**（不一次性回填）

- 不建议给 45→47 篇主档 ADR 加必填 `归属域` 字段：① S12 的既有教训是头部字段缺失=**静默退出全部校验**（`check_governance_surface.py:30-36` 的 #1524/#2035），一次性回填的可见性反而是假象；② 47 篇 = 碰共享元文件，与在途 ADR（0043–0047 等）PR 必然串行/冲突；③ F3 已给出不依赖 ADR 侧字段的等价实现（表→ADR 反查）。
- 若 owner 仍要字段：用**稳定行 ID**（`SO-01` 式）而非 `§N`（表内插行会重编号；先例：`PROJECT_REVIEW_PLAN.md:30`「编号 R01–R15 保持稳定，不随审查顺序调整而重新编号」）；缺失必须显式列「未映射 ADR 清单」，禁止静默。
- 与 ADR-0034 的衔接：本表是共享元文件 → 同一时间单 Execution 修改（AGENTS.md「共享元文件」款）；declare 一律带 `--issue`（`execution-contract.md:113-115` 决策类纪律），避免 §3.4 查重静默通过。

### F7 验收可测性 —— **观察**（issue 建议的判据需改写）

- 「出现新的双标时 S15 必红」**不可能**（F3 判据①）。
- 可测替代三条：① X1–X3 各收敛为**一行** owner 且锚点可解析（S15-② 绿）；② S15-③ 对「ADR 改版本而表未动」的合成样例双向自证（`--self-test`，同 S14 先例）；③ 首个观测对象 = F-1 窗口：ADR-0039 转 Accepted 的 PR 内 `AGENTS.md:15` 是否同步（可用 `git diff` 人工核）。
- 「被至少一处代码注释 S14 引用链验证」与归属收敛无因果，不构成判据，建议删。

---

## 4. 附：复测命令与口径

```bash
# 基线
git -C <worktree> rev-parse HEAD                      # ee75d173（证据采集起于 497952c8）
# F-2 / F-6
grep -c '权威' docs/adr/ADR-*.md | sort -t: -k2 -nr
grep -n 'Tier [123]' docs/adr/ADR-0033*.md            # :91-93
# F-3（口径=git grep -l，排除自身 ADR 文件）
git grep -l "ADR-0020" -- . ':(exclude)docs/adr/ADR-0020*' ':(exclude)docs/adr' | wc -l   # 169
# F-4 / F-5
ls -d backend/agent/scripts/*/ | wc -l                # 34
find backend/agent/scripts -maxdepth 2 -type d -name 'v*' | wc -l                          # 179
find backend/agent/scripts -name '*.py' -exec cat {} + | wc -l                             # 107464
grep -n '刷机\|flash' docs/adr/ADR-0033*.md                                                # 0 命中
# F-1 / S11
sed -n '15p' AGENTS.md; sed -n '270,282p' tools/dev/check_governance_surface.py
git grep -n '不可原地修改或删除' -- .                  # 仅 AGENTS.md:15 与转述
# X1 代码真源
sed -n '525,575p' backend/services/plan_dispatcher_core.py
sed -n '12,43p' backend/services/precheck/scripts.py   # :24 select Script.content_sha256; :36
git grep -n 'script_meta\b' -- . ':!backend/agent/scripts'   # 仅 0021 文本 + 转述，无实现键
```

**未覆盖 / 局限**：未做全库审计（Review effort 3/5）；F-3 计数口径与 issue 不同源，绝对值不可混用；本稿不裁决 ADR-0033 v1.3 内容（按 issue 纪律归另线）。

**落盘与关联**：本稿经 `declare --requirement review-semantic-ownership-codebuddy --harness codebuddy --worktree /tmp/stp-2546-codebuddy --role review --scope docs/reviews --issue 2546 --test-impact none`（Mode C 并行，`--force` 留痕）落盘；结论待 synthesis 汇聚，本稿不修改任何 ADR 正文、不做 Accepted 裁决、不开实现单。
