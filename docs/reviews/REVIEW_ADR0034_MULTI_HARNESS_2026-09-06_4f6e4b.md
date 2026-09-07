# ADR-0034 多 Harness 执行契约只读审查（v0.3）

- **状态**：Living（审查快照；不代改被审文档）
- **日期**：2026-09-06
- **会话 resume**：`55fdb7c6-2fae-4644-aa24-ad70674f6e4b`（后缀 `4f6e4b`）
- **审查对象**：[`docs/adr/ADR-0034-multi-harness-execution-contract.md`](../adr/ADR-0034-multi-harness-execution-contract.md)（**Proposed v0.3**）
- **关联合入**：PR [#858](https://github.com/DUElost/stability-test-platform/pull/858)（v0.1）、[#859](https://github.com/DUElost/stability-test-platform/pull/859)（v0.2）、[#860](https://github.com/DUElost/stability-test-platform/pull/860)（v0.3 Contract hardening，commit `864da462`）、[#861](https://github.com/DUElost/stability-test-platform/pull/861)（README M7 → v0.3）
- **关联 Issue**：[#855](https://github.com/DUElost/stability-test-platform/issues/855)、[#857](https://github.com/DUElost/stability-test-platform/issues/857)、[#854](https://github.com/DUElost/stability-test-platform/issues/854)
- **并行审查**：同日已有两份 v0.3 只读审查——
  [`…_0cd302`](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_0cd302.md)（正文内部问题 / 实施歧义角度）、
  [`…_9c124`](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_9c124.md)（机械校验角度）。
  本文为独立第三份（契约空白 + 一致性角度），与前两份交集处显式交叉引用，不重复展开。

## 1. 结论摘要

v0.3 的「六项必修 + 三项完善」方向正确，实质性修复 v0.2 三处硬伤：状态模型两维
正交（overlap=集成窗口）修正「仅 ACTIVE 参与」的语义错误；registry root 落
`git-common-dir` 天然解决多 worktree 唯一性与免跟踪；MERGED 只认 GitHub 事实杜绝
registry 僭越。与引用基线（deepseek 对照 note、Phase -1 基线 note）及周边文档
（repository-workflow、harness-adapters、2026-09-04 note）一致性核对无冲突；附录 A
实测补上了 G2「先实测再裁决」的前置，G2 从条件采纳转正式裁决的闭环成立。

**技术内容可以接受**。但有 3 处契约定义层空白建议 Accepted 前钉死（见 §3）——
v0.3 自身精神是「不留给实现自由解释」（§2.8 标题），这几处恰恰留下解释空间。

## 2. 方法与范围

只读审查：`864da462`（v0.3）对 v0.2 的 diff + ADR 全文（§1–§6、附录 A）+ draft
note 增量（`2026-09-06-adr-0034-draft.md`）+ README M7 状态行 + 引用方一致性抽查
（AGENTS.md / repository-workflow.md / harness-adapters.md / 2026-09-04 note）。
未运行任何命令修改工作区。

## 3. Accepted 前建议钉死的契约空白

与前两份审查的重叠说明：**A1/A2 与 0cd302 §4.1-1/2/3 同题**，本审查从「事实来源
分层表自身不完整」角度复述一遍以保持自洽，不再另造编号；**A3 与 0cd302 §4.2-8
同题**（该文件从「ADR 变第二契约源」角度，本审查补迁移处置句）。

### A1. READY / ABANDONED 的判定者与写入者未定义（§2.3）

事实来源分层表只对 MERGED 特指「只能由 GitHub PR 状态确认」，并约束 `ai_work`
不得单方面写 MERGED；但 `PR_OPEN→READY` 由谁判定、由谁写入，`ABANDONED` 由谁
触发，均未规定。留白则 P1 各实现自行发明语义。建议补一句：READY 由
`ai_work update/status` 依据 GitHub PR 的 required-checks 状态派生刷新（事实刷新
非人工宣称）；ABANDONED 只能由显式人工动作触发，永不由超时/命令自动产生。

### A2. STALE 是「展示层派生」还是「持久字段」，§2.3 与 §2.5 口径打架

§2.3 写「超时只标 STALE 提示人工确认」；§2.5 P1 写「不自动改写任何字段」。若
STALE 落库则矛盾，若永不落库则 P1 的 liveness 字段只有 ACTIVE 一个持久值。
建议统一：**持久只存 `last_seen`；ACTIVE/STALE 为 status 展示层派生**（last_seen
+ TTL 计算），P2 heartbeat 就位后方可将 STALE 落为持久字段——与「last_seen 在
P1 不是 liveness 权威」自洽。

### A3. §2.10 分家后，ADR 正文 §2.1–2.9 存量内联规范去向未定

P0 建立 `execution-contract.md` 后，ADR §2 内联的完整规范与 contract.md 形成双份
事实；ADR 在本仓库是 living doc（v0.2→v0.3 即实例），「细则不回填 ADR 正文」只
管未来、管不住已内联部分。建议 P0 行补迁移处置：迁移时 ADR §2 收敛为决策要点 +
指向 execution-contract.md，或显式标注「§2 为 Accepted 时快照，权威以
execution-contract.md 为准」。（0cd302 §4.2-8 同题，方向一致。）

## 4. 建议（非阻塞）

### B1. 章节顺序：§2.8/2.9/2.10 位于 §2.7 分期表之后

分期表 P0/P1 行前向引用 §2.8/§2.9/§2.10。建议把 2.8–2.10 移到 2.7 之前（§2.6
后直接接 2.8），消除前向引用。

### B2. coverage-mismatch 的「CI 证据」来源口径（§2.9）

PR required checks 不含完整 backend/frontend tests（full 走手工 workflow/夜间）。
若证据源是 PR checks，则每个 `direct` 声明都可能常态亮 mismatch。建议指明证据
来源 = PR checks + 手工 full workflow 运行记录，或明确「接受该噪声为 advisory
设计意图」。（未见前两份审查覆盖，属本审查新增项。）

### B3. registry 可见性边界补一句（§2.2）

`git-common-dir` 保证同克隆多 worktree 唯一，但**同一机器两个独立克隆不共享
registry**。现状派生视图同为 per-clone，语义一致；建议显式写「registry 按克隆
隔离，跨克隆无共享」，防多克隆场景误读为全局登记。（新增项。）

### B4. `git-common-dir` 输出可能是相对路径

取决于 cwd / GIT_DIR 环境；实现需 realpath 归一化（P1 实测项已隐含，可在 §2.2
加「输出先归一化」字样）。（新增项。）

### B5. Role Context 未列入 §2.10 contract.md 内容清单

§2.1 模型含 Role Context、P2 交付它，但 2.10 清单只列「状态模型、scope 语法、
registry 协议、drift/coverage 语义」。建议补入，或在 §2.1 注明其定义归属 P2。
（与 0cd302 §4.2-6 不同，该文件修的是 G2 路径写法，本条为清单完备性。）

### B6. 编辑级

① P0 行「五处 canonical」与 §2.10 图「四入口 + 一权威」数字口径统一；② G2 试点
（`backend/agent/`→`aee/`，共享元文件串行 PR）与 P0 同为元文件接线的先后关系
可加半句「P0 合入后接 G2 试点」；③ Verification P0 行「（现 63 行）」改「（预算
内）」，避免行数快照漂移。（①②③均为新增；G2 路径写法 `aee/` →
`backend/agent/aee/` 已由 0cd302 §4.2-6 提出，本文不重复。）

## 5. 一致性核对（通过项）

- deepseek 对照 note（引用基线）G1–G5：G1/G3/G4 采纳描述与 AGENTS/notes README/
  checker 现状一致；G2 裁决、G5 Revisit 与 note 建议顺序一致 ✓
- 取代关系：2026-09-04 note「Accepted 后生效」→ P0 标 superseded + 交叉链接，
  与 note 生命周期约定自洽 ✓
- repository-workflow.md / harness-adapters.md 的 2026-09-04 指针 → P0 接线计划
  覆盖 ✓
- README 索引 v0.3（#861）、ADR 头状态行 v0.3、draft note 修订记录三者同步 ✓
- §4 Alternatives 新增两行（overlap 仅 ACTIVE / P1 硬 TTL）与 §2.3/§2.5 对应 ✓
- Verification P1 自测清单与 §2.3/§2.5/§2.8/§2.9 Contract 条款一一对应 ✓
- 9c124 的六项必修机械核验（全部通过）与本文不冲突，本文未重复机械核验 ✓

## 6. 审查结论

Proposed v0.3 技术内容可接受；§3 三处契约空白（READY/ABANDONED 判定者、STALE
落库口径、§2 内联规范迁移处置）建议 Accepted 前定稿——其中前两处与 0cd302 同题，
采纳时以 0cd302 §4.1 + 本文 §3 合并为一条修订即可。§4 为编辑级建议，可随
Accepted 前微修 PR 一并处理。

## 7. v0.4 复审（2026-09-06 追加）

- **审查对象**：[ADR-0034](../adr/ADR-0034-multi-harness-execution-contract.md)
  **v0.4 终局** commit `1715eee6`（PR [#862](https://github.com/DUElost/stability-test-platform/pull/862)
  八源 synthesis + [#863](https://github.com/DUElost/stability-test-platform/pull/863)
  R6/R18 人工裁决落地）
- **本文归属**：v0.3 部分已作为八源之一被
  [`synthesis`](REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md) 收录并随
  #862 入库（R 编号为唯一权威映射）；本节为对 v0.4 终局的只读复审（同一会话）。

### 7.1 本文 v0.3 发现的处置核验（→ R 编号）

| 本文 v0.3 发现 | synthesis 裁决 | v0.4 落点核验 |
|---|---|---|
| A1 READY/ABANDONED 写入权威（§3） | R2 采纳 | ✅ READY=GitHub checks 派生刷新可回退、CLOSED=GitHub 事实、ABANDONED 仅显式 `finish --abandon` |
| A2 STALE 持久 vs 派生（§3） | R3+R4 采纳 | ✅ liveness 查询时派生、持久只存 `last_seen`、`status` 严格只读（观察不改状态） |
| A3 ADR §2 存量规范去向（§3） | R9 采纳 | ✅ P0 一次性平移、ADR §2 收缩为决策要点+指针 |
| B1 章节重排（§4） | 不采纳 | ✅ 理由成立：随 R9 P0 平移消解，避免大段移动错位；Proposed 窗口内的前向引用属可读性成本，非契约缺口 |
| B2 coverage-mismatch 证据口径（§4） | R13 采纳 | ✅ 夜间全量/合并后记录，非 PR 轻量 checks |
| B3 per-clone 可见性边界（§4） | 随 R1 采纳 | ✅ §2.2「Registry 按克隆隔离，不构成全局登记」 |
| B4 git-common-dir 相对路径（§4） | R1 采纳 | ✅ 升级为 `--path-format=absolute` 唯一发现方式 + 删外置落点 + §5 三位置解析验收 |
| B5 Role Context 入 contract 清单（§4） | 未见对应 R | ⚠️ 残差 → 本节 N4 |
| B6 ①五处 canonical 口径 / ②G2 顺序 / ③63 行快照（§4） | R8 / R19 / R14 | ✅ ①②落地；③P0 行已删快照，但 §1 仍留「63 行」表述（事实引用，非 P0 快照问题，不再提） |

### 7.2 新发现（v0.4，N1–N5）

**N1（建议 Accepted 前补一句）`lifecycle=ABANDONED × integration=PR_OPEN` 悬空组合未定义**
overlap 集合 = `lifecycle ∉ {ABANDONED}`——若执行者在 PR 仍开着时 `finish
--abandon`，该 PR 的集成窗口即刻从 registry 消失，另一 Execution 改同文件时收不
到 overlap 提示（registry 认为已放弃，PR 实际还活着）。契约定义了僵尸出口
（STALE+零 diff→候选→abandon），未定义「abandon 时 PR 未关」这一侧。建议 §2.3
加一句：`finish --abandon` 在 integration=PR_OPEN 时应提示先关闭/转交 PR，或
transition table（P0 contract 必备目录）显式列出该组合处置。

**N2（R14 同族漏网）README M7 看板行版本残留 v0.3**
主表（`docs/adr/README.md:85`）已补 v0.4 完整行 ✅；**M7 里程碑看板行（:97）仍为
「Proposed v0.3」**——正是 #861 修过的那类「版本修订漏更」，#862 只补了主表。

**N3（R14 措辞族漏网）Alternatives「Phase 1」术语未统一**
ADR §4 Alternatives「Phase 1 即引入 heartbeat daemon / TTL 硬语义」行仍保留
「Phase 1」（§2.5 已全用 P1）。

**N4（承接 B5）Role Context 定义归属仍悬空**
§2.1 模型含 Role Context、P2 交付「上下文供给」；§2.10 contract.md 内容清单仍只
列状态模型/scope 语法/registry 协议/drift/coverage，未含 Role Context 语义归属。
建议 §2.1 注一行「Role Context 的定义与供给细则入 contract / 由 P2 定义」。

**N5（轻微）P1 启动判据数据源未指明**
「连续两周并行 worktree ≥3」——registry 是 P1 产物不能自证；判据应从 git
worktree 历史/日志统计（与派生视图同数据源）。实施可解，contract 注明即可。

### 7.3 通过项与一致性（v0.4，抽查）

- #863 裁决背景句（§2.3「三维=实现选择而非冻结条款，lifecycle/finished_at 二选
  一」）与 synthesis「裁决后补记」、draft note「裁决落地」三处一致 ✅
- R15 归因修正（S1–S10→#853、S11→#856）、R21 #847 对账与 Alternatives 窄口径、
  R22 P1 启动判据、#855 三段触发，均落地 ✅
- R14 族：DOC-MAP 行（v0.4）、governance 文档 L0 行 S1–S11、draft note 锚 v0.4
  并补 v0.4/v0.3 修订记录 ✅（漏网见 N2/N3）
- `venv/bin/python tools/dev/check_governance_surface.py --check` 通过（S1–S11、
  S5x）✅

### 7.4 复审结论

v0.4 技术内容**可以 Accepted**：核心契约空白（R2/R3/R6/R9 族）已全部钉死，人工
裁决（R6 三维定位、R18 Competition 否决）收口干净。本节发现均不构成阻断——
N1 建议 Accepted 前在 §2.3 补一句约束（或作为 P0 transition table 强制目录项）；
N2–N5 可随 Accepted 前微修 PR 一并处理（合计约 5 行改动）。

## 8. ADR-0034 开发工作完结确认（2026-09-07 追加，只读盘点）

- **性质**：第三轮审查（v0.5，结论见上轮会话交付的 F1–F3）之后，对 ADR-0034 从
  Accepted 到分期实施完成的终局盘点；只读，未改任何文件
- **时点**：2026-09-07；盘点基线 = origin/main（当时 tip `3e072f62`）

### 8.1 结论

ADR-0034 的契约决策、评审收敛与全部分期实施均已完成，开发工作收口。

### 8.2 契约生命周期（完结）

- **Accepted v1.0**（PR #865，2026-09-06 用户人工终审批准）→ v1.1（#866 细则
  迁出）→ **v1.2**（#877 P1 启动判据修订：已计划批次启动前预置就绪）
- 版本链完整：v0.1(#858)–v0.5(#864)→v1.0(#865)→v1.1(#866)→v1.2(#877)
- 评审闭环：R1–R30 两轮八源 synthesis 全部裁决完结；8 份审查文件 + synthesis
  入库；上轮 F1（§2.4「以 diff 为准」与并集口径冲突）在细则迁出时已消解——
  `execution-contract.md` 内无该句残留，仅「并集恒成立 + drift 提示」（§5.1）
- 细则权威源：`docs/development/ai/execution-contract.md` Living **v1.1**（10 节），
  ADR 收缩为决策要点 + 指针，冲突以 contract 为准已明示

### 8.3 分期实施（P0a–P3 完结，P4 按设计不启动）

| 期 | PR | 落地 |
|---|---|---|
| P0a | #866 | contract 建立 + ADR §2 细则迁出（v1.1） |
| P0b | #869 | supersede 标注（09-04 note 头部已标「已被 ADR-0034 Accepted v1.0 取代」）+ 薄入口接线 |
| G2 试点 | #870+#873+#875 | scoped AGENTS.md 真身 + CLAUDE.md symlink 薄壳；Cursor/Codex 补测后 **4/4 全通过** |
| P1 | #878 | `tools/dev/ai_work.py` Execution Registry MVP；`check:quick` 已含 ai-work gate |
| P2 | #879 | Adapter（whoami + heartbeat 指引） |
| P2b | #892 | Claude 根 bootstrap 供给 + 加载矩阵终验 |
| P3 | #893 | drift gate（advisory）——commit 自述「ADR-0034 分期最后一块」 |
| P4 | — | Integration Planner：观察项，不启动 = 正确终态 |
| #854 | #876 | 治理门禁逃逸向量修复，issue CLOSED |

### 8.4 收尾残差（3 项，均非未完结的开发工作）

1. **README 版本滞后**：ADR 头已 v1.2，但 `docs/adr/README.md` 主表与 M7 看板行
   仍写 v1.0——v1.1/v1.2 两次修订均漏更（同族缺陷第 N 次复发）。一行同步。
2. **#855 [OPEN] deferred**：行为验证缺口——跟踪项（机制已随 P3 drift gate 落地），
   issue 闭环待实际运行验证后由人关闭。
3. **#857 [OPEN]**：Claude `@import` 子目录解析缺陷——工具链侧缺陷，ADR Revisit
   跟踪项（根层 import 形态复评触发条件）；G2 已以 symlink 形态绕开，不阻塞。

### 8.5 现场一致性

工作树干净、本地与 origin 同步；`check_governance_surface.py --check` 全绿
（S1–S11、S5x）；`ai_work.py` 存在且已接线 run_gates（check:quick 含 ai-work；
ai-drift 为独立 advisory）。

## 9. 批次第一单完成确认（2026-09-07 追加，只读核验）

- **性质**：多 Harness 批次第一单（#880 codec 修复）交付声明的只读对证；P1
  交付物（ai_work）首次实战验证记录
- **核验对象**：main tip `3e072f62`（PR #899）；叙事声明逐项对证

### 9.1 逐项核验

| 声明 | 核验 | 证据 |
|---|---|---|
| main tip = `3e072f62`，#899 合入 | ✅ | tip 即 PR #899 merge（`fix/880-registry-codec`，MERGED 2026-09-07T04:55:46Z）；含 ReDoS 修复 `6ce81f80` 与 retrigger `db3d3276` |
| #880 自动 CLOSED | ✅ | `[closed]`，标题精确匹配：「ai_work.py registry codec：requirement 以 '#' 开头或含 ': ' 时 declare 报 [OK]、随后所有命令崩（#878 引入）」 |
| 修复 1+2+3 全落 | ✅ | `normalize_requirement_id` + REFUSED 路径；`_quote` 含 # 注释防护（行首裸 # 会被当注释）+ dump key 走 `_quote`；corrupt-<stamp> 隔离 + `load_locked` + 「#880 三缺口红绿」自测 |
| Registry 两条完整生命周期 | ✅ | `.git/ai-work/registry.yaml`（`--path-format=absolute` 落位正确）：`fix-825-gates-parity` 与 `fix-880-registry-codec`，均 FINISHED / integration_cache=MERGED / 含 pr_number、test_impact、scope 全字段 |
| Dogfood 工具链实战 | ✅ | `ai_work.py status`（契约规定严格只读）实况：两条记录 `integration=MERGED risk=no`——真值表「MERGED 出局」行为正确 |
| 工作树干净 | ✅ | main 与 origin 同步，无未提交改动 |
| CodeQL 修复 | ✅ | PR #899 内独立 commit `6ce81f80`（ReDoS 消除）；合入本身证明 required checks 通过 |

### 9.2 一处叙事偏差（认知修正，不影响结论）

**「#880 是 R01 架构审查台账（#891）的发现」不成立**：#891 台账全文 10 项为
R01-F01–F10，对应 **#881–#890，全部是平台架构项**（SID registry 续期、PlanUpdate
422、lifespan 清理、TESTING=1 加载 .env.backend、/health SAQ、REDIS_URL 明文、
SID unregister 原子性、版本门禁、表名单数、领导选举 fail-open）——**不含 #880**。
#880 编号低于 #881（先于台账 issues 登记），标题标注「（#878 引入）」，属工具链
自身使用/审查链路的独立发现。优先级判定（工具链崩溃级、阻塞批次使用）不受影响。

### 9.3 结论

叙事主体事实全部属实：批次第一单已完成真实闭环——修复落码、红绿自测、CI 含
CodeQL 通过、合入 main、issue 自动关闭、Registry 留下两条 MERGED 终态真实记录。
多 Harness 批次可以正式开跑；需保留的认知修正是 #880 的发现归属（非 R01 台账
项）。
