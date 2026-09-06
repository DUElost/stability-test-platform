# ADR-0034：多 Harness 并行执行契约与执行登记（Multi-Harness Execution Contract）

- 状态：**Proposed（v0.5 草案，待人工评审）**
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
Agent 间**不通信、不共享上下文、不实时协调**——Parallel Execution + Asynchronous Visibility + **Repository-Mediated Integration**（仓库是唯一媒介）。

**选择权原则**：用哪个 Harness 承接哪个 Requirement，**始终由开发者决定**（延续 2026-09-04 约定与现行实践——开发者亲自启动并驱动各 Harness）。本契约**不定义任何需求路由或自动下发机制**：上述箭头链只描述**溯源**（哪个 Requirement 由哪个 Harness 的哪个 Execution 承接），不描述**指派**（谁该做什么）；Registry 记录由执行侧自行 `declare`（visibility-only，供可见性与审计），不是调度器。

### 2.2 Execution Registry（P1 落地）

- 工具 `tools/dev/ai_work.py`：`declare / status / update / finish`（含 `finish --abandon`）子命令 + overlap 检测；
- **Registry root = `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`**——`--path-format=absolute`（git ≥ 2.31）是**唯一发现方式**：裸 `--git-common-dir` 在主 checkout 返回 cwd 相对路径（仓库根 `.git`、子目录 `../../.git`）、linked worktree 返回绝对路径，行为不一致且裸拼接会算错。不硬编码 `.git`，不提供 common dir 之外的替代落点（防多 Registry 分裂与 NFS/CIFS 落位）。目录内固定两文件：`registry.yaml`（数据）+ `registry.lock`（flock 锁，同目录）；位于 `.git` 内天然不被跟踪。**Registry 按克隆隔离**——同一机器多个独立克隆不共享 registry，与派生视图同口径（per-clone），不构成全局登记；
- **写入协议（九步全序，硬约束）**：`flock(registry.lock) → read registry.yaml → validate → modify → write same-dir registry.yaml.tmp → fsync(tmp) → rename(tmp, registry.yaml) → fsync(parent dir) → unlock`——文件 fsync 不保证 rename 后目录项的崩溃持久性，故必须补父目录 fsync。异常类型、残留 tmp 清理与损坏恢复由 `execution-contract.md` 定义。**仅本地 FS 成立，禁止落 NFS/CIFS**；
- Registry **不对业务文件/scope 上锁（visibility-only）**；`registry.lock` 仅保护 registry 文件自身的原子写；
- **effective scope = `normalized(declared) ∪ derived(diff)`（并集恒成立）**：`derived(diff)` 是 Git 事实、声明不能覆盖或删除它；`declared` 无论有无 diff 都保留为意图（Registry 的核心新增价值恰是 diff 出现前、以及 diff 尚未覆盖全部计划范围时的意图可见性）。两者不一致时输出 **declaration drift 提示**（advisory）——不是丢弃声明，也不静默放行；`update` 可覆写声明（声明过期由执行侧显式清理，见 6d0f05 O-1 的残留面）。`derived(diff)` 口径分档：worktree 在场 → 工作树 diff（tracked staged/unstaged + untracked，untracked 口径 = `git ls-files --others --exclude-standard`——`git diff --name-only` 不含新文件，新建文件同样是集成风险）；worktree 已删除（finish 后常见）→ branch diff（`merge-base..branch`）；两者皆不可得 → 回落到声明单独生效。实测反例（声明与 diff 偏离须提示 drift 而非静默采信任一方）：2026-09-04 `docs/drift-sync-*` 声明 `docs`、实际 diff 触及 `backend/` 与 `.github/`。overlap 参与集合见 §2.3。

### 2.3 状态模型：lifecycle × liveness × integration 三维正交

每条记录三个正交字段。**裁决背景（2026-09-06 人工确认）**：用户确认的 Contract v1 为**两维**（Execution state × Integration state，核心约束 = STALE 不退出集成风险窗口）；评审修订后（9261bd B3）的真实阻断点是 **`finish` 缺少独立、可持久化的语义表达**——两维下「先开 PR、继续编码、再 finish」路径无字段可落，修订允许「独立 lifecycle 字段」或「两维 + `finished_at`」二选一。本 ADR 选**前者**（lifecycle 同时承载 `ABANDONED` 的执行侧放弃语义，覆盖「编码中途放弃、无 PR」路径）——三维是**实现选择而非冻结条款**：

- **lifecycle ∈ {CODING, FINISHED, ABANDONED}**（执行侧自声明）：`CODING`=编码中；`FINISHED`=`finish` 写入（执行者已停止编码，**只写本字段、不碰 integration**——与 PR 先后无关）；`ABANDONED`=**仅显式人工动作**（`finish --abandon`），永不因超时/命令自动产生；
- **liveness ∈ {LIVE, STALE}**（**永远 advisory、查询时派生、不持久化**）：持久层只存 `last_seen`；STALE = `now − last_seen > TTL` 的展示层派生值，P1 不回写。STALE ≠ 死、≠ 可回收、**不退出集成窗口**、不影响任何业务语义；
- **integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}**（GitHub 权威）：`NO_PR`=未登记 PR；`PR_OPEN`=已登记 PR 号；`READY`=required checks 全绿（由 `update` 依 GitHub checks **派生刷新**，非人工宣称；主干推进致 checks 重跑则回退 `PR_OPEN`；不区分 FIFO 队首位置）；`MERGED`/`CLOSED`=终态（合入 / PR 关闭未合），均只能由 GitHub PR 状态确认。

**overlap（集成风险）集合由真值表定义——开放 PR 永不被本地执行侧状态遮蔽**（v0.5 依四源复审共振修正：v0.4 的 `lifecycle ∉ {ABANDONED} 且 integration ∈ {NO_PR, PR_OPEN, READY}` 会让 `ABANDONED × PR_OPEN` 悬空组合静默退出窗口——执行者放弃了，PR 还开着还在等 FIFO；也与 `CODING × CLOSED` 同病，即 R2 所堵之洞换了入口重开）：

```text
risk = integration ∈ {PR_OPEN, READY}                                ← GitHub 事实：开放 PR 恒在窗口
    OR (integration = NO_PR    AND lifecycle ∈ {CODING, FINISHED})   ← 无 PR 但执行侧未放弃
    OR (integration = CLOSED   AND lifecycle ≠ ABANDONED)            ← PR 被关 ≠ 工作停止（误关/被取代/reopen）
```

`MERGED` 出局（变更已进主干，风险真实关闭；merge 后继续新工作应重新 declare）。`liveness` 不参与。典型反例仍成立：Execution A `finish` 后 Harness 退出（STALE），其 PR 仍改着 `foo.py`，新 Execution B 改 `foo.py` 时必须仍能看到 overlap 提示。**`finish --abandon` 不再是「立即出窗」**：无开放 PR 时记录出窗（僵尸出口的唯一合法终点）；有开放 PR 时记录留在窗口直到 GitHub 侧终态——`finish --abandon` 在 `integration ∈ {PR_OPEN, READY}` 时必须警告并提示先关闭/转交 PR（转手 = 新 Execution 重新 `declare`）。僵尸候选清单：`status` 输出「lifecycle ∈ {CODING, FINISHED} 且 STALE 且 effective scope 为空」记录，人工经 `finish --abandon` 收口。完整组合矩阵与并发刷新顺序入 P0 transition table。

**事实来源分层**（Registry 从不僭越权威）：

| 事实 | 权威来源 |
|---|---|
| declaration / execution metadata（scope、Role、PR 号、lifecycle、时间戳） | Registry（执行侧自声明） |
| actual diff / branch / commit | Git（不一致时以 diff 为准，§2.4） |
| PR lifecycle（**MERGED / CLOSED / READY 的 checks 事实只能由 GitHub 确认**） | GitHub |
| verification | CI |

`finish(PR #N)` 的语义仅为：**执行者已停止编码且登记 PR 号**（lifecycle→FINISHED，integration→PR_OPEN 仅当尚为 NO_PR）；`ai_work` 不得单方面写 `MERGED`/`CLOSED`——`update` 落终态前必须核对 GitHub PR 状态。完整 transition table（含 reconcile 与 GitHub 不可用时的降级）为 P0 `execution-contract.md` 必备目录（§2.7）。

### 2.4 声明与 diff 的关系（diff 优先）

Registry 声明与实际 diff 不一致时**以 diff 为准**；派生视图（对 merge-base 取差异，含未提交）保留为 ground truth，Registry 是补充而非替代。

### 2.5 TTL 与心跳（分期）

- **命令语义**：`status` **严格只读**（观察不得改变被观察状态——不刷任何记录的 `last_seen`）；仅携带 execution identity 的写命令（`declare/update/finish`）顺带刷新**自身** `last_seen`；
- **P1（无 heartbeat daemon）**：TTL（24h 量级）**仅 advisory**——STALE 为查询时派生（§2.3），超时只在 status 输出提示「可能陈旧」并列出候选清单，不自动改写任何持久字段、不剔除、不降级。声明式 CLI 之间没有可靠心跳源，此期 `last_seen` 不是 liveness 权威；
- **P2（Harness wrapper/adapter 提供 heartbeat）**：`last_seen` 方可升格为可靠 liveness 信号；STALE 仍为派生展示（或明确唯一回写者），advisory 语义不变。

### 2.6 并发上限（保留）

显式上限保留 ≈2-3。理由迁移：逐 PR 决策已政策化给 auto-merge（approvals=0 + FIFO），人的注意力从「审 PR」转移到「审审计面」——瓶颈仍是人的吞吐，上限只是换了守的对象。**正面回应 2026-09-04 原结论**：审阅瓶颈未被证伪也不打算缓解——Registry 不提升审阅吞吐，提升的是审计面的**信息完备性**（谁在做什么、集成窗口在哪）；「任务排队」仍是主策略，上限不放宽。

### 2.7 分期

| 期 | 内容 | 备注 |
|---|---|---|
| P0 | 规则先行：**建立 `docs/development/ai/execution-contract.md`（Execution Contract 唯一权威源，§2.10），§2.2/2.3/2.5/2.8/2.9 细则一次性平移入内（必备目录：transition table 含 §2.3 真值表全部组合与并发刷新顺序、reconcile 来源、GitHub 不可用降级、drift 对 Agent Note 等强制随附物的豁免规则、scope 组件边界 overlap 谓词、`lifecycle` 术语与 pipeline_def 域 lifecycle（S11 锚定）的消歧注记、持久字段清单（liveness 不在其中）、P1 启动判据的数据源口径（git worktree 历史/日志统计，与派生视图同源——registry 是 P1 产物不能自证）、Role Context 定义归属）**，**平移合入时本 ADR 升 v1.1 并在版本记录注明「细则已迁出至 execution-contract.md，本文保留决策要点」**（Accepted 正文不被无痕改写）；AGENTS.md/CLAUDE.md 改写走**独立 docs PR**（元文件串行化）；**单一 canonical Contract + 明确列举薄入口**（AGENTS.md/CLAUDE.md/`.cursor/rules`/`.codex`——各入口只保留最小启动原则与指针，勿手工镜像）；`harness-adapters.md`、`repository-workflow.md` 与 Phase -1 基线 note 的并行约定指针接到本文；`execution-contract.md` 入治理门禁（S2 `link_files` + S6 `RESIDENT_BUDGETS`） | **负载最重一期，建议拆 P0a（契约文档）+ P0b（接线/门禁/supersede）两个 PR**；合入后接 G2 试点（§3） |
| P1 | Registry MVP（ai_work.py + registry.yaml/registry.lock 按 §2.2 + 三维状态与 overlap 集合按 §2.3 + scope MVP 按 §2.8 + `test_impact` 字段入 schema（§2.9，允许缺省）+ 自测红绿样例） | **启动判据**：连续两周并行 worktree ≥3，或实际发生 ≥2 次跨 Harness 撞车返工——未触发则维持 2026-09-04 派生视图用法 |
| P2 | Harness Adapter：各 Harness 会话启动时知晓自身 Role——**上下文供给，非路由**（会话由开发者选择启动，Adapter 只保证该会话能读到 Role Context 与**根启动契约 + scoped 内容**）；提供 heartbeat（§2.5 升格条件）；**验收含 cwd 深度 × Harness 加载矩阵**（附录 A 协议扩展） | |
| P3 | 真增量 = **Drift / Freshness gate**：先 advisory（本地 run_gates / 夜间全量，守合入路径 ~2min 注意力预算），overlap 粒度用顶层目录作 hint 而非硬门禁；含 `coverage-mismatch` advisory（§2.9） | **不建 merge queue**——主干机制已存在（FIFO enable-auto-merge + update-branch + strict 分支保护） |
| P4 | Integration Planner：仅在「人已难判集成顺序」真实积累后启用 | 观察项 |

### 2.8 Scope MVP 边界（P1 Contract 即约束，不留给实现自由解释）

- scope 表达 = **repo-relative path**，仅 file 或 directory 两种粒度，归一化（normalized）后存储；**明确拒绝**：绝对路径、含 `..` 的路径、仓库内 symlink 逃逸到仓库外、trailing slash 歧义——overlap 匹配采用**路径组件边界**谓词（`backend` 与 `backend_new` 不重叠），细则入 P0 contract；
- MVP 明确**不支持**：glob、ownership 语义、自动任务拆分、semantic scope、任何形式的 locking；
- **Role 不是文件 ownership 边界**（延续 2026-09-04 约定第 2 条精神：分片是冲突规避手段，不是职责边界）——overlap 只是 hint，从不禁止跨 scope 修改。

### 2.9 test_impact 声明与 coverage-mismatch（Contract 先定义，实现后置）

- `declare` 附 `test_impact ∈ {none, direct, indirect}`：none=不改行为语义（纯文档/注释等）；direct=直接改测试或被测代码；indirect=可能影响行为的非直接改动。**P1 允许缺省**（缺省视同 indirect 并在 status 提示）——不为分类摩擦付协同税；
- coverage 评估 = **Harness 声明 × CI 证据**合成：声明 `none` 但 diff 触及测试相关路径、或声明 `direct` 但无对应测试运行记录 → `coverage-mismatch`，**advisory**，归 P3 drift gate 家族（Drift / Verification）。**CI 证据口径 = 夜间全量 / 合并后 CI 运行记录**（非 PR 轻量 checks——PR 路径有意不含全量 backend/frontend 测试，按 PR checks 判定会让 `direct` 声明常态误报），与合入路径 ~2min 注意力预算原则联动；
- 本节先入 Contract；P1 仅落 registry schema 字段，检测实现允许后置至 P3。

### 2.10 契约权威源（ADR 与契约文档分家）

```text
docs/development/ai/execution-contract.md   ← Execution Contract 唯一权威源（single authority）
        ↑ 最小引用
AGENTS.md / CLAUDE.md / .cursor/rules / .codex    ← 各入口只保留最小启动原则与指针
```

- `execution-contract.md` 承载执行语义完整规范（状态模型、scope 语法、registry 协议、drift/coverage 语义），细则演进在该文档版本化，不回填 ADR 正文；
- ADR-0034 本身 = 方向裁决记录（决策、理由、取代关系）；
- `AGENTS.md` 仍为 **minimal bootstrap contract**：保留最少量不可遗漏的启动原则（总原则/硬不变量/按需入口），加一行指向 execution-contract.md——**不空壳化，也不复制执行语义**。

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
| auto mode 默认化 / 提高并发上限 | 08-26 synthesis 裁决前提（治理面写者 >1 常态化、auto mode）未满足；并发瓶颈见 §2.6 |
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
| Antigravity CLI | **未验证（延期）** | Phase -1 时未安装，未纳入矩阵——不因缺席而视为通过；安装后按同协议补测 |

**延伸矩阵（#857）**：Claude `@AGENTS.md` import 解析——仓库根 ✅ / 子目录 ❌（`-p` 与 TUI 双模式，引文诊断证实字面行未展开、AGENTS.md 五章节零出现；cwd 相对存在同名文件亦不解析）。
