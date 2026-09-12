# ADR-0034：多 Harness 并行执行契约与执行登记（Multi-Harness Execution Contract）

- 状态：**Accepted（v1.12）**
- 版本记录：v0.1 #858 / v0.2 #859（选择权原则）/ v0.3 #860（Contract hardening）/ #861（索引同步）/ v0.4 #862（八源 synthesis）+ #863（R6/R18 裁决）/ v0.5 #864（第二轮复审）/ v1.0 #865（**Accepted**，2026-09-06 用户人工终审批准）/ v1.1 #866（§2 细则迁出至 `execution-contract.md`，本文保留决策要点 + 指针）/ **v1.2 #877：P1 启动判据修订——增补「已计划的多 Harness 批次启动前预置就绪」（2026-09-07 用户裁决：本 ADR 立项背景即即将开展的多 Issue 集中修复与新需求开发，工具须先于场景就绪；判据全文见契约 §9 v1.1）**
**v1.3 本版：附录 A 增补 Antigravity CLI 实测（2026-09-07，`agy 1.1.26 -p`：无仓库规则自动发现——根/嵌套 AGENTS.md、CLAUDE.md symlink、GEMINI.md 均不加载，引文诊断确认；供给=调用方前置 `tools/dev/agy_with_rules.sh`；P2 加载矩阵终验随之扩展为五家结论）**
**v1.4 本版：附录 A 补机制层根因（规则装载=声明式配置 `user_rules` 节空被 skip——装载清单无约定文件通道）与官方迁移文档冲突记录（迁移文档声称解析 active directory 的 GEMINI/AGENTS.md，但 `-p` 非交互实测不符——待上游确认，澄清前 agy 供给一律走前置脚本）**
**v1.5 #914：附录 A 分层装载实测补全——全局层（~/.gemini/GEMINI.md 与 ~/.gemini/AGENTS.md）在 -p 下均装载、workspace 层仍全部不装载；仓库规则供给维持 agy_with_rules.sh 前置**
**v1.6 本版：Antigravity 定性裁决（用户 2026-09-07）——「带规则的高级顾问」，不纳入可承接 Requirement 的 Harness 名单（headless 工具循环三路径崩溃、无法独立完成 Execution 周期）；Registry `--harness` 不做名单硬校验，上游修复复测后可升格**
**v1.7 本版：Role 定位收敛（用户 2026-09-08 裁决）——Role=保留的 Execution 元数据与未来扩展点，当前默认且唯一实际运行角色为 `implementation`（registry 空串视为缺省），特殊 Role 暂不进入主执行路径；§2.7 P2 的「会话启动时知晓自身 Role」从必交付降级为 deferred capability（不要求 Harness 启动时自动注入、不要求所有 Harness 对所有 Role 等价支持），不为「完成 P2」补建 Role 运行机制；原契约「role 定义与供给细则归 P2 Adapter」条款同步撤回，契约 §1.2 role 行已重写。P2 现存待交付项仅剩 heartbeat wrapper**
**v1.8 本版：Role 收敛 Revisit 两项闭环（用户 2026-09-08 裁决）——①role 缺省归一化：declare 缺省写入 implementation（历史空串同义读取、不迁移），实现 ai_work.py `default_role` 同 PR；②Role 扩展再开启条件成文：仅「声明面消费 Role」的真实需求（差异化登记纪律/门禁判定/overlap 处理）构成触发，经用户裁决走契约新版本 + ADR 增补，「多一种标签写法」不构成触发。细则均落契约 §1.2**
**v1.9 本版：并发上限反转（用户 2026-09-08 裁决）——§2.6 移除「≈2-3 显式上限」与「上限不放宽」：该数字自 2026-09-04 约定未实测继承，多 Harness 批次实际常态为 5+ 会话并行（含单 Harness 多开），早已被常态超出而无机械强制，且与本 ADR 立项目的（为多 Harness 并行 AI Coding 建立协同机制）自相矛盾；瓶颈原则校准为「在集成收尾侧（人的审阅吞吐 + 外部平台可靠性），不在 agent 并行侧」，守的对象从会话数重锚为在窗 Execution（risk 集合）规模与集成收尾负载；「任务排队」主策略与同文件串行排程不变；§4 Alternatives 对应行拆分改写、§6 增实测数据触发器；契约 §8 同 PR 原子同步、adr/README 与 DOC-MAP 索引行同步（S12 口径）；本版号 v1.8 已被并行 #1018（Role 收敛闭环）占用，合并期重编 v1.9**
**v1.10 本版：附录 A 增补 dsh web 实测（2026-09-08，DeepSeek Harness `dsh` 0.1.1-rc.2，headless 阳性对照 + 浏览器自动化驱动 web UI）——根级 `AGENTS.md` 基线注入 ✅、scoped `AGENTS.md` 触碰后动态注入 ✅（会话 typed source `kind=agent-instructions` 实证；web 会话 cwd=工作区根，「cwd 深度」验收形态不适用）；⚠️ 静态 `--dump-config`/patch 层显示该插件 `disabled: true` 与运行时行为矛盾——加载判定只认行为探针；调用前提=工作区经原生目录选择器注册（GUI 无脚本通道）；Registry CLI 未 dogfood，转正以首个真实单为准**
**v1.11 本版：dsh web 转正回填——Registry CLI 全周期 dogfood 通过（#1256：declare→worktree 修复→gates→PR #1291→update→finish，2026-09-10 合入；0.1.5-rc.1 加载复测与 v1.10 结论一致），附录 A 行与 harness-adapters.md 行同步更新**
**v1.12 本版：CodeBuddy CLI/IDE 分立——附录 A 原单行「CodeBuddy」实为 CLI 结论却被读作覆盖整个产品线（IDE 从未探针）；2026-09-11 人工补测 IDE 得 Q1=否/Q2=是/Q3=一次（Zcode 同形态，与 CLI 相反），故照 Cursor CLI/IDE 分列先例拆为两行、CLI 版本按实测校正为 2.149.0，harness-adapters.md 与 harness_probe.py 同步（IDE 入人工形态）；**IDE 版本 4.11.3 经人工读取补入本版**（探针时未能从磁盘读出）**
- 优先级：P1
- 目标里程碑：M7（延续）
- 日期：2026-09-06
- 决策者：平台研发组
- 标签：multi-harness, execution-registry, worktree, drift-gate, agents-md, #855, #857
- 关联 Issue：[#855](https://github.com/DUElost/stability-test-platform/issues/855)（行为验证缺口补全）、[#857](https://github.com/DUElost/stability-test-platform/issues/857)（子目录 import 解析缺陷）、[#854](https://github.com/DUElost/stability-test-platform/issues/854)（门禁缺口，非阻塞）
- 引用基线：[`2026-09-05-deepseek-harness-convention-study.md`](../notes/process/2026-09-05-deepseek-harness-convention-study.md)（G1-G5 事实边界）、[`2026-09-05-ai-harness-convention-baseline.md`](../notes/process/2026-09-05-ai-harness-convention-baseline.md)（Phase -1 基线，#853）
- 多 Harness 评审：[`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](../reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)（八源审查综合裁决，v0.4 修订的输入证据；R 编号为唯一权威映射）
- 取代对象（Accepted 后生效）：[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)（多 Agent 并行开发约定，含 #847「不为 N=2 引入 WIP 公告类机制」裁定——**对账**：本 ADR 的 Registry 与被否决的 WIP 公告不同在于①**advisory/visibility-only**（从不构成约束或 ownership；手写状态「会过期而你会信它」的否决理由由 `declared ∪ derived`、derived 为准正面化解——声明不构成事实来源）②**前提已变**（多 Harness 引擎并行已实测可用，非 N=2 单机形态）；「任务排队、不为 N 引入协同机制」的主策略不变）

---

## 1. 背景与问题定性

现行并行约定（2026-09-04 note + AGENTS.md「开始任务时」）是同引擎多会话形态下的最小机制：派生视图看 diff、元文件串行化、并发上限 ≈2-3，冲突靠**避免**而非**机制**。该约定的适用前提正在消失：

1. **多 Harness 已实测可用**（2026-09-06 矩阵，附录 A）：Codex 0.153.0 / Cursor Agent 2026.09.02 / OpenCode 1.18.25 在本机均能非交互执行并自动摄取仓库契约；Claude Code 2.1.259 经 tinno 旁路恢复可用。
2. **共享契约层已收敛**（Phase -1，PR #853 + PR #856）：AGENTS.md 63 行最小启动契约 + 硬不变量 + 按需入口，S1–S10 结构门禁随 #853 入 CI、S11 硬不变量锚点随 #856 入 CI。
3. **deepseek 基线 G1/G4/G3 已采纳**：硬不变量跨 Harness 可见、证据纪律、note 取代规则与基础校验。

缺的恰好是执行层语义：

- **无登记**——「哪个 Harness 在哪个 worktree 做哪个 Requirement」无机器可读事实，派生视图只能看本机 diff；
- **无状态语义**——「编码完成」与「已集成」混为一谈，overlap 的生命周期没有终点定义；
- **无漂移防护**——并行声明了 scope 之后，集成前契约是否漂移不可见；
- **加载层有实测缺口**——#857：Claude 从子目录启动时根 CLAUDE.md 的 `@import` 不解析（-p 与 TUI 双模式同病），多 Harness 执行恰以深层目录为常态。

## 2. 决策：执行契约主体

### 2.1 核心模型

`Requirement → Harness → Execution（= Worktree + Role Context + Registry 记录）`。
Role Context 当前形态即 Registry `role` 元数据（默认 `implementation`；运行时供给为 deferred capability，v1.7）——不是路由、不是 ownership 边界。
Agent 间**不通信、不共享上下文、不实时协调**——Parallel Execution + Asynchronous Visibility + **Repository-Mediated Integration**（仓库是唯一媒介）。

**选择权原则**：用哪个 Harness 承接哪个 Requirement，**始终由开发者决定**（延续 2026-09-04 约定与现行实践——开发者亲自启动并驱动各 Harness）。本契约**不定义任何需求路由或自动下发机制**：上述箭头链只描述**溯源**（哪个 Requirement 由哪个 Harness 的哪个 Execution 承接），不描述**指派**（谁该做什么）；Registry 记录由执行侧自行 `declare`（visibility-only，供可见性与审计），不是调度器。

### 2.2 Execution Registry（P1 落地）— 细则见契约 §2

**决策要点**：`tools/dev/ai_work.py`（declare/status/update/finish --abandon）；Registry root 以 `git rev-parse --path-format=absolute --git-common-dir` 为**唯一发现方式**（落 `$(...)/ai-work/`，per-clone 隔离，禁止 NFS/CIFS）；九步原子写全序为硬约束；visibility-only 不对业务上锁；effective scope = `declared ∪ derived(diff)` 并集恒成立 + drift 提示（derived 三分档：worktree/branch/声明）。完整协议（写入异常处置、损坏恢复、untracked 口径、scope 语法与 overlap 谓词）见 [`execution-contract.md` §2/§5](../development/ai/execution-contract.md)。

### 2.3 状态模型：lifecycle × liveness × integration 三维正交 — 细则见契约 §3

**裁决背景**：用户确认的 Contract v1 为两维（Execution × Integration）；真实阻断点是 `finish` 缺独立可持久化表达，修订允许独立 lifecycle 字段或 `finished_at` 二选一——本 ADR 选 lifecycle（兼载 `ABANDONED` 放弃语义），**三维是实现选择而非冻结条款**。

**决策要点**：lifecycle{CODING,FINISHED,ABANDONED}（执行侧；ABANDONED 仅显式人工）；liveness{LIVE,STALE}（永远 advisory、查询时派生、不持久化）；integration{NO_PR,PR_OPEN,READY,MERGED,CLOSED}（GitHub 权威；READY 派生刷新可回退）。**overlap 集合由真值表定义——开放 PR（PR_OPEN/READY）恒在风险窗口，不被本地 lifecycle 遮蔽**；CLOSED 不单独出局；MERGED 出局。完整真值表、T1–T8 transition table、reconcile 与 GitHub 不可用降级见 [`execution-contract.md` §3](../development/ai/execution-contract.md)。

**事实来源分层**（Registry 从不僭越权威）：declaration/metadata → Registry（自声明）；actual diff/branch/commit → Git；PR 生命周期（MERGED/CLOSED/READY checks）→ **GitHub**；verification → CI。`ai_work` 不得单方面写终态。

### 2.4 声明与 diff 的关系（diff 优先）

Registry 声明与实际 diff 不一致时**以 diff 为准**；派生视图（对 merge-base 取差异，含未提交）保留为 ground truth，Registry 是补充而非替代。

### 2.5 TTL 与心跳（分期）— 细则见契约 §4

**决策要点**：`status` 严格只读（观察不改变被观察状态）；写命令刷自身 `last_seen`；P1 无 heartbeat daemon 故 TTL 仅 advisory（不改字段/不剔除/不降级），P2 有 heartbeat 后 `last_seen` 方可升格。分期语义见 [`execution-contract.md` §4](../development/ai/execution-contract.md)。

### 2.6 并发与审计吞吐（v1.9 反转）

**不设会话数上限**。原「≈2-3 显式上限」（v1.0–v1.8）自 2026-09-04 约定未实测继承：多 Harness 批次实际常态为 5+ 会话并行（含单 Harness 多开；2026-09-07/08 批次实测 6-7 并发会话），数字被常态超出而无机械强制——被常态违反的规范不是限制而是文档漂移，按「以实际为准、同步权威文档」纪律于本版移除。

- **瓶颈模型（校准）**：瓶颈在**集成收尾侧**——人的审阅吞吐 + 外部平台可靠性（GitHub checks / auto-close 故障窗、gh 串行化），**不在 agent 并行侧**。Registry 不提升审阅吞吐，提升的是审计面的**信息完备性**（谁在做什么、集成窗口在哪）；
- **守的对象重锚**：真实约束的可观测代理 = **在窗 Execution（§3.2 risk 集合）规模 + 集成收尾负载**（合入后核销、reconcile、冲突返工），由开发者按批次调度——上限由实测数据表达而非文档数字；数据恶化时按 §6 重议；
- **「任务排队」仍是主策略**：FIFO auto-merge 串行集成、同文件显式串行排程不变；无 PR 的评审 / scratch 会话不计入约束；
- 会话数 ≠ worktree 数 ≠ 在窗 Execution 数：registry 只统计已 declare 的 Execution（评审/scratch 会话按 #919 指引同样 declare），三者以 registry + `git worktree list` 组合观测。

### 2.7 分期

| 期 | 内容 | 备注 |
|---|---|---|
| P0 | **P0a（本版已交付）**：`execution-contract.md` 建立、细则一次性平移、本 ADR 收缩升 v1.1。**P0b（独立 docs PR）**：AGENTS.md/CLAUDE.md 改写（元文件串行化）；单一 canonical Contract + 薄入口接线（AGENTS.md/CLAUDE.md/`.cursor/rules`/`.codex`）；`harness-adapters.md`、`repository-workflow.md` 与 Phase -1 基线 note 的指针接到本文；`execution-contract.md` 入治理门禁（S2 `link_files` + S6 `RESIDENT_BUDGETS`）；supersede 2026-09-04 note（含 §9 过渡条款保留） | P0b 合入后接 G2 试点（§3） |
| P1 | Registry MVP（ai_work.py 按 [`execution-contract.md`](../development/ai/execution-contract.md) §2–§5 实现 + `test_impact` 入 schema（允许缺省）+ 自测红绿样例） | **启动判据**见契约 §9 v1.1（v1.2 增补第一触发：已计划的多 Harness 批次启动前预置就绪）；就绪并采用前维持派生视图用法（过渡条款） |
| P2 | Harness Adapter：提供 heartbeat（§2.5 升格条件）；**验收含 cwd 深度 × Harness 加载矩阵**（附录 A 协议扩展）。**v1.7 修订：Role Runtime（会话启动时知晓/注入自身 Role）从本行必交付降级为 deferred capability**——Role 现阶段定位=元数据+扩展点（§2.1；契约 §1.2 v1.5，默认 `implementation`），不要求 Harness 启动时自动注入、不要求所有 Harness 对所有 Role 等价支持 | |
| P3 | 真增量 = **Drift / Freshness gate**：先 advisory（本地 run_gates / 夜间全量），overlap 粒度用顶层目录作 hint 而非硬门禁；含 `coverage-mismatch` advisory（契约 §6） | **不建 merge queue**——主干机制已存在（FIFO enable-auto-merge + update-branch + strict 分支保护） |
| P4 | Integration Planner：仅在「人已难判集成顺序」真实积累后启用 | 观察项 |

### 2.8 Scope MVP 边界 — 细则见契约 §5.3/§5.4

**决策要点**：repo-relative file/directory 归一化；拒绝 absolute/`..`/symlink 逃逸/trailing slash；不支持 glob/ownership/自动拆分/semantic/locking；**Role 不是文件 ownership 边界**；overlap 谓词 = 路径组件边界前缀。谓词定义见 [`execution-contract.md` §5.4](../development/ai/execution-contract.md)。

### 2.9 test_impact 声明与 coverage-mismatch — 细则见契约 §6

**决策要点**：`test_impact ∈ {none, direct, indirect}`，P1 允许缺省（=indirect）；coverage 评估 = 声明 × CI 证据 → `coverage-mismatch`（advisory，P3 实现）；**CI 证据口径 = 夜间全量 / 合并后记录**（非 PR 轻量 checks）。定义见 [`execution-contract.md` §6](../development/ai/execution-contract.md)。

### 2.10 契约权威源（ADR 与契约文档分家）

```text
docs/development/ai/execution-contract.md   ← Execution Contract 唯一权威源（single authority）
        ↑ 最小引用
AGENTS.md / CLAUDE.md / .cursor/rules / .codex    ← 各入口只保留最小启动原则与指针
```

- `execution-contract.md` 承载执行语义完整规范（术语与数据模型、registry 协议、状态模型与 transition table、scope 语法与 overlap 谓词、test_impact/coverage 语义、drift 豁免、启动判据与过渡条款），细则演进在该文档版本化，不回填 ADR 正文（**本版 v1.1 已完成迁出**）；
- ADR-0034 本身 = 方向裁决记录（决策、理由、取代关系）；
- `AGENTS.md` 仍为 **minimal bootstrap contract**：保留最少量不可遗漏的启动原则（总原则/硬不变量/按需入口），加一行指向 execution-contract.md——**不空壳化，也不复制执行语义**（接线在 P0b）。

## 3. G2：scoped 上下文文件命名与形态（本 ADR 内裁决）

**现状**：`backend/agent/{,aee/}CLAUDE.md` 内容中立却用 Claude-only 命名；非 Claude Harness 无自动加载（仅根规则人工路由）。

**裁决**：迁移为「scoped `AGENTS.md` 真身（中立内容）+ `CLAUDE.md` 薄壳」：

- **形态优先级：symlink > `@import`**。依据：#857 实证 `@import` 在子目录 cwd 下不解析（-p 与 TUI 双模式）；symlink 在文件系统层生效、与 cwd 无关（deepseek 上游与业界推荐的另一形态，此处获反面实证支撑）。**symlink 方向固定为 `CLAUDE.md → AGENTS.md`**（真身只此一份）；**注意：symlink 消除的是双份内容漂移，不提供写保护**——经 `CLAUDE.md` 路径写入会穿透修改真身，写路径限制需 checker / hook / 明确操作规则（如「编辑一律落 `AGENTS.md` 真身」的约定）。symlink 若被工具链（Windows 协作 / 特定构建）拒绝，退回 `@import` 并以 **#857 修复确认为前置**。
- **试点顺序**：`backend/agent/` → `backend/agent/aee/`；迁移走共享元文件串行 PR，排在 P0 合入之后。
- **checker 同步**：S6 预算表加 scoped AGENTS.md 条目；S2 `link_files` 加新路径；根层 CLAUDE.md 的 import 形态（S8 已锁）**本次不动**，待 #857 修复后另行评估是否 symlink 化。
- **验收**：四家 Harness 以 cwd=目标目录跑附录 A 探针协议，**scoped 真身内容与根启动契约（总原则/硬不变量）同时可见**——#857 已证明 scoped symlink 不自动解决根契约供给，P2 Adapter 必须明确根 bootstrap 供给方案（如会话从仓库根启动或显式注入），验收不得只验 scoped 单边。

## 4. Alternatives（已考虑并否决）

| 备选 | 否决理由 |
|---|---|
| 维持 2026-09-04 约定，不引入 Registry | 派生视图遍历 `git worktree list`，实已覆盖本机全部 worktree（含各 Harness 的 worktree）——**真实缺口比直觉窄**：仅当两个 Execution 均未产出任何 diff 时，声明意图无载体可见；且声明与 diff 的偏离（2026-09-04 实测：声明 `docs`、实际触及 `backend/`）无留痕可审计。多 Harness 常态化后该缺口从偶发变结构性，故补 Registry（§2.2 的 `declared ∪ derived` 使两者互补而非替代） |
| P3 建 merge queue | 主干机制已存在（FIFO auto-merge + update-branch + strict）；真增量是 drift/freshness 检测（修正⑥） |
| G2 维持 CLAUDE.md 命名 | 3/3 非 Claude Harness 实测读嵌套 AGENTS.md（附录 A）；维持等于放弃已验证的加载通道 |
| symlink 全局替换（含根层） | 根层 S8 已锁 import 形态且 #857 仅证实子目录缺陷；根层迁移待 #857 修复后独立评估，不随本 ADR 捆绑 |
| 维持 ≈2-3 会话数上限（v1.0–v1.7 原裁决） | **v1.9 反转**：数字未实测、被多批次 5+ 常态超出而无机械强制，且与本 ADR 立项目的矛盾；守对象重锚见 §2.6 |
| auto mode 默认化 | 08-26 synthesis 裁决前提（治理面写者 >1 常态化、auto mode）仍未满足；与人驱动多会话并行为正交两轴，不随 v1.8 并发放开而松动 |
| overlap 仅看 liveness（当时术语 ACTIVE，即现 LIVE） | finish 后 STALE 的在途变更仍是集成风险窗口（§2.3 反例）；集成窗口与执行者活性是两个正交维度 |
| P1 即引入 heartbeat daemon / TTL 硬语义 | 声明式 CLI 之间无可靠心跳源，硬 TTL 会把「上午 declare、全天编码」的长任务误判（§2.5 分期：P1 advisory，P2 有 heartbeat 后再升格） |

## 5. Verification

- **P0**：建立 `execution-contract.md`（细则一次性平移，ADR §2 收缩为决策要点+指针）并完成薄入口接线（AGENTS.md/CLAUDE.md/`.cursor/rules`/`.codex` + `harness-adapters.md` + `repository-workflow.md` + 基线 note）；AGENTS.md 在 80 行/8KB 预算内完成 supersede 改写；`execution-contract.md` 入 S2/S6 门禁；治理门禁 S1–S11 全绿；2026-09-04 note 标注 superseded 并交叉链接本文。
- **P1**：`ai_work.py` 自测红绿样例（含 STALE 派生 advisory、三维状态、overlap 集合分支、**「声明 scope ≠ 实际 diff」fixture**（复刻 2026-09-04 反例）、scope 拒绝规则）；registry root 实测——主 checkout 根 / 主 checkout 子目录 / linked worktree 三处经 `--path-format=absolute` 解析到同一绝对路径；`test_impact` 字段入 schema（缺省=indirect）；`check:quick` 全绿。
- **P2**：cwd 深度 × Harness 加载矩阵（附录 A 协议，含根 bootstrap + scoped 双边可见）全部通过后，Adapter 方可视为就绪；heartbeat 就位后 `last_seen` 升格。
- **P3**：drift gate（含 `coverage-mismatch`，证据口径=夜间全量/合并后记录）以 advisory 上线，夜间全量含其自测；转 required 须独立裁决。
- **G2 试点**：四 Harness 探针验收（scoped 真身 + 根契约同时可见）+ S6/S2 扩展后门禁绿。
- **#855 收口（三段触发）**：Git merge（已完成）= 草案可被引用；**ADR Accepted = 方向生效**；**P0 完成 = #855 补全工作可开工**。三选一方向（引擎可插拔行为 eval / 每 Harness 确定性摄取自检 / 并入 drift gate 邻接验收）在 P1 实施期裁决——**#857 正是其防范故障类的现实实例**（L0 全绿下的语义传导断裂，仅行为层探针能发现）。

## 6. Revisit

- **G5**（`.agents/` 单家目录 / skills 多消费方）：新增受版本控制的 harness 适配时，按 [`harness-adapters.md`](../development/ai/harness-adapters.md) 修改顺序重估；
- **auto mode 成为默认工作态**：重访行为验证挂载强度（2026-08-26 synthesis 重议条件，现状见 #855）；
- **审计吞吐实测恶化**（集成冲突/返工率、合入后核销与 reconcile 负载、登记交互成本上升）：重议 §2.6 并发姿态与收尾自动化（如 post-merge 自动 reconcile）——触发器是实测数据，非会话数；
- **AGENTS.md 逼近 80 行/8KB ceiling**：预算扩容须独立裁决，不随功能顺手放宽；
- **#857 上游修复**：根层 import 形态与 G2 形态优先级随之复评；
- **Competition mode**（显式、受审计的开发者批准竞争）：**已裁决（2026-09-06）**——冻结版 Contract v1 不含此条款，评审建议降级为非阻断追溯项，不入 Contract；现文本 overlap=hint + 不上锁已隐含允许并行，真实竞争需求出现再议。

## 附录 A：2026-09-06 Harness 摄取实测矩阵

协议：/tmp 一次性 worktree，`backend/agent/` 放嵌套 `AGENTS.md`（含唯一探针串），各 Harness 以 cwd=该目录非交互启动，单问双题禁用工具（Q1=阳性对照「## 总原则/## 提交前」标题可见性，Q2=探针串可见性）。

| Harness | 嵌套 AGENTS.md 自动发现 | 调用前提（坑） |
|---|---|---|
| Codex 0.153.0 | ✅ live | DeepSeek API 余额；官方文档逐级发现口径一致 |
| Cursor Agent 2026.09.02 | ✅ live | 非交互需 `--trust` |
| OpenCode 1.18.25 | ✅ live | 需本机 `opencode.json`（未跟踪）在启动目录树内 |
| Claude Code 2.1.259 | ❌（子目录通道=CLAUDE.md） | 需显式 `--settings`（alias 对脚本不生效）；`unrecognized_model` 警告无害 |
| Antigravity CLI（agy 1.1.26） | ❌ 实测（2026-09-07）：根/嵌套 `AGENTS.md`、`CLAUDE.md`（含 symlink）、`GEMINI.md` 均不自动加载——探针+引文+日志+stream-json 四重证据；机制=声明式配置 `user_rules` 节空被 skip；**与官方迁移文档声称的 GEMINI/AGENTS 解析冲突，待上游确认**；供给=调用方前置（`tools/dev/agy_with_rules.sh`） | `agy -p` 非交互可用；**定性=带规则的高级顾问，不承接 Requirement/Execution（2026-09-07 用户裁决）** |
| Cursor IDE 3.17.19 | ✅（2026-09-07 人工补测）：子目录工作区根+scoped 双边可见，与 cursor-agent CLI 同引擎对齐（双份加载 Q3=2 同 CLI）；Registry CLI 可用 | IDE Agent 人工探针（GUI 无脚本通道）；无需根供给（根 AGENTS.md 自动加载） |
| Zcode 3.11.2（GUI） | ⚠️（2026-09-07 人工补测）：**子目录打开只装载 workspace 的 `AGENTS.md`，根不注入**（Q1=否/Q2=是——与 #857 互补的缺口形态）；可发现性已由 scoped 真身头部根指针覆盖（实测『总原则』在引述文字可见） | GUI 无 CLI 探针通道；Registry CLI 可用（三单 dogfood 即 Zcode 会话）；P2 动作表已补「文档/评审类会话同样 declare」指引（#919） |
| CodeBuddy CLI（2.143.1 首测→2.149.0 复测） | ✅ live（2026-09-07 首测 + 2026-09-11 复测一致）：子目录 cwd 根+scoped 双边可见、单份加载（Q1=是/Q2=是/Q3=一次）；机制侧 `[MemoryLoader] Loaded 1 memory rules: [project] [always] …/AGENTS.md` | `codebuddy -p` 非交互可用、零配置；Registry CLI 与 P2 动作表全程可用（110 条记录、FINISHED×MERGED 100） |
| CodeBuddy IDE 4.11.3（GUI） | ⚠️（2026-09-11 人工补测）：**子目录打开只装载 workspace 的 scoped `AGENTS.md`，根不注入**（Q1=否/Q2=是/Q3=一次——**Zcode 同形态**，与上方 CLI 结论相反）；可发现性由 scoped 真身头部根指针部分覆盖（实测『总原则』仅在引述文字可见） | GUI 无脚本通道；Registry CLI 未 dogfood、未转正；**与 CodeBuddy CLI 是分立实体**——同厂商不同加载通道，照 Cursor CLI/IDE 分列先例，不得互相外推 |
| dsh web（DeepSeek Harness，0.1.1-rc.2 首测→0.1.5-rc.1 复测） | ✅ 根级基线注入 + ✅ scoped 触碰后动态注入（2026-09-08/09-11 两版本探针行为一致；同路径重复触碰去重）：会话 cwd=工作区根，基线只注入根级（typed source `kind=agent-instructions` 实证）；scoped 在首次 read/write/edit 触碰该目录后动态注入（`backend/agent/AGENTS.md` read 实测）——「cwd 深度」验收形态不适用；⚠️ 静态 `--dump-config`/patch 层 `disabled: true` 与运行时矛盾，加载判定只认行为探针 | 工作区经原生目录选择器注册（GUI 无脚本通道，自动化不可驱动）；headless profile 同插件 root→cwd 基线全通（阳性对照）；**Registry CLI 全周期 dogfood 通过（#1256→PR #1291，2026-09-10 合入）——已转正** |

**延伸矩阵（#857）**：Claude `@AGENTS.md` import 解析——仓库根 ✅ / 子目录 ❌（`-p` 与 TUI 双模式，引文诊断证实字面行未展开、AGENTS.md 五章节零出现；cwd 相对存在同名文件亦不解析）。
