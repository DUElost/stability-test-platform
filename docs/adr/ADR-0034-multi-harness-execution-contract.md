# ADR-0034：多 Harness 并行执行契约与执行登记（Multi-Harness Execution Contract）

- 状态：**Proposed（v0.3 草案，待人工评审）**
- 优先级：P1
- 目标里程碑：M7（延续）
- 日期：2026-09-06
- 决策者：平台研发组
- 标签：multi-harness, execution-registry, worktree, drift-gate, agents-md, #855, #857
- 关联 Issue：[#855](https://github.com/DUElost/stability-test-platform/issues/855)（行为验证缺口补全）、[#857](https://github.com/DUElost/stability-test-platform/issues/857)（子目录 import 解析缺陷）、[#854](https://github.com/DUElost/stability-test-platform/issues/854)（门禁缺口，非阻塞）
- 引用基线：[`2026-09-05-deepseek-harness-convention-study.md`](../notes/process/2026-09-05-deepseek-harness-convention-study.md)（G1-G5 事实边界）、[`2026-09-05-ai-harness-convention-baseline.md`](../notes/process/2026-09-05-ai-harness-convention-baseline.md)（Phase -1 基线，#853）
- 取代对象（Accepted 后生效）：[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)（多 Agent 并行开发约定，含 #847「不为 N=2 引入 WIP 公告类机制」裁定）

---

## 1. 背景与问题定性

现行并行约定（2026-09-04 note + AGENTS.md「开始任务时」）是单人单 Harness 形态下的最小机制：派生视图看 diff、元文件串行化、并发上限 ≈2-3，冲突靠**避免**而非**机制**。该约定的适用前提正在消失：

1. **多 Harness 已实测可用**（2026-09-06 矩阵，附录 A）：Codex 0.153.0 / Cursor Agent 2026.09.02 / OpenCode 1.18.25 在本机均能非交互执行并自动摄取仓库契约；Claude Code 2.1.259 经 tinno 旁路恢复可用。
2. **共享契约层已收敛**（Phase -1，PR #853）：AGENTS.md 63 行最小启动契约 + 硬不变量 + 按需入口，S1-S11 确定性门禁与常驻预算入 CI。
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

- 工具 `tools/dev/ai_work.py`：`declare / status / update / finish` 子命令 + overlap 检测；
- **Registry root = `$(git rev-parse --git-common-dir)/ai-work/`**——所有 worktree 经同一 common dir 解析到同一位置（多 worktree 场景天然唯一），目录内固定两文件：`registry.yaml`（数据）+ `registry.lock`（flock 锁，与数据文件同目录）；位于 `.git` 内天然不被跟踪（无需 gitignore 条目），若实现选择 common dir 之外路径则必须入 `.gitignore`；
- 写入协议：flock `registry.lock` → same-dir temp + fsync + 原子 rename，**仅本地 FS 成立，禁止落 NFS/CIFS**；
- Registry **只暴露 scope 声明、从不上锁**；overlap 检测看**集成窗口**而非活跃度——参与集合见 §2.3。

### 2.3 状态模型：liveness 与 integration 两维正交

每条记录两个正交字段：

- **liveness ∈ {ACTIVE, STALE}**——执行者活性，**永远 advisory**：超时只标 STALE 提示人工确认，从不改变记录的业务语义（STALE ≠ 死、≠ 可回收、≠ 退出集成窗口）；
- **integration ∈ {NO_PR, PR_OPEN, READY, MERGED, ABANDONED}**——集成事实：`NO_PR`=编码中未开 PR；`PR_OPEN`=已开 PR；`READY`=required checks 全绿、进入 FIFO 集成位；`MERGED`/`ABANDONED`=终态（合入 / 明确放弃）。

**overlap 集合 = integration ∈ {NO_PR, PR_OPEN, READY}，liveness 不参与**。理由：编码停止、执行者退出的在途变更仍处于集成风险窗口——典型反例：Execution A `finish` 后 Harness 退出转 STALE，其 PR 仍改着 `foo.py`，新 Execution B 改 `foo.py` 时必须仍能看到 overlap 提示。「等集成」是显式中间态：**overlap 生命周期终于 merge（或放弃），非编码结束**。

**事实来源分层**（Registry 从不僭越权威）：

| 事实 | 权威来源 |
|---|---|
| declaration / execution metadata（scope、Role、PR 号、时间戳） | Registry（执行侧自声明） |
| actual diff / branch / commit | Git（不一致时以 diff 为准，§2.4） |
| PR lifecycle（**MERGED 只能由 GitHub PR 状态或等价权威来源确认**） | GitHub |
| verification | CI |

`finish(PR #N)` 的语义仅为：**执行者已停止编码且已提交 PR**（`NO_PR→PR_OPEN`）；`ai_work` 不得单方面写 `MERGED`——`update` 落终态前必须核对 GitHub PR 状态。

### 2.4 声明与 diff 的关系（diff 优先）

Registry 声明与实际 diff 不一致时**以 diff 为准**；派生视图（对 merge-base 取差异，含未提交）保留为 ground truth，Registry 是补充而非替代。

### 2.5 TTL 与心跳（分期）

- **P1（无 heartbeat daemon）**：`status/update` 等命令顺带刷新 `last_seen`；TTL（24h 量级）**仅 advisory**——超时只在 status 输出提示「可能陈旧」，不自动改写任何字段、不剔除、不降级。声明式 CLI 之间没有可靠心跳源，此期 `last_seen` 不是 liveness 权威；
- **P2（Harness wrapper/adapter 提供 heartbeat）**：`last_seen` 方可升格为可靠 liveness 信号；STALE 判定仍只作用于提示层（§2.3 的 advisory 语义不变）。

### 2.6 并发上限（保留）

显式上限保留 ≈2-3。理由迁移：逐 PR 决策已政策化给 auto-merge（approvals=0 + FIFO），人的注意力从「审 PR」转移到「审审计面」——瓶颈仍是人的吞吐，上限只是换了守的对象。

### 2.7 分期

| 期 | 内容 | 备注 |
|---|---|---|
| P0 | 规则先行：**建立 `docs/development/ai/execution-contract.md`（Execution Contract 唯一权威源，§2.10）并完成最小引用接线**；AGENTS.md/CLAUDE.md 改写走**独立 docs PR**（元文件串行化）；规则单一权威源（AGENTS.md/CLAUDE.md/.cursor/.codex/docs 五处 canonical + 最小引用，勿手工镜像）；`harness-adapters.md` 与 Phase -1 基线 note 的并行约定指针接到本文（基线 note Revisit 的既定要求） | 本文 Accepted 后第一个 PR |
| P1 | Registry MVP（ai_work.py + registry.yaml/registry.lock 按 §2.2 + overlap 集合按 §2.3 + scope MVP 按 §2.8 + `test_impact` 字段入 schema（§2.9）+ 自测红绿样例） | |
| P2 | Harness Adapter：各 Harness 会话启动时知晓自身 Role——**上下文供给，非路由**（会话由开发者选择启动，Adapter 只保证该会话能读到 Role Context 与共享契约）；提供 heartbeat（§2.5 升格条件）；**验收含 cwd 深度 × Harness 加载矩阵**（附录 A 协议扩展） | |
| P3 | 真增量 = **Drift / Freshness gate**：先 advisory（本地 run_gates / 夜间全量，守合入路径 ~2min 注意力预算），overlap 粒度用顶层目录作 hint 而非硬门禁；含 `coverage-mismatch` advisory（§2.9） | **不建 merge queue**——主干机制已存在（FIFO enable-auto-merge + update-branch + strict 分支保护） |
| P4 | Integration Planner：仅在「人已难判集成顺序」真实积累后启用 | 观察项 |

### 2.8 Scope MVP 边界（P1 Contract 即约束，不留给实现自由解释）

- scope 表达 = **repo-relative path**，仅 file 或 directory 两种粒度，归一化（normalized）后存储；
- MVP 明确**不支持**：glob、ownership 语义、自动任务拆分、semantic scope、任何形式的 locking；
- **Role 不是文件 ownership 边界**（延续 2026-09-04 约定第 2 条精神：分片是冲突规避手段，不是职责边界）——overlap 只是 hint，从不禁止跨 scope 修改。

### 2.9 test_impact 声明与 coverage-mismatch（Contract 先定义，实现后置）

- `declare` 附 `test_impact ∈ {none, direct, indirect}`：none=不改行为语义（纯文档/注释等）；direct=直接改测试或被测代码；indirect=可能影响行为的非直接改动；
- coverage 评估 = **Harness 声明 × CI 证据**合成：声明 `none` 但 diff 触及测试相关路径、或声明 `direct` 但 CI 无对应测试运行记录 → `coverage-mismatch`，**advisory**，归 P3 drift gate 家族（Drift / Verification）。Git/CI 能覆盖的只是「实际变更 × 已有测试」的语义；测试证据本身是并行执行模型的一部分，故入 Contract；
- 本节先入 Contract；P1 仅落 registry schema 字段，检测实现允许后置至 P3。

### 2.10 契约权威源（ADR 与契约文档分家）

```text
docs/development/ai/execution-contract.md   ← Execution Contract 唯一权威源（single authority）
        ↑ 最小引用
AGENTS.md / CLAUDE.md / .cursor / .codex    ← 各入口只保留最小启动原则与指针
```

- `execution-contract.md` 承载执行语义完整规范（状态模型、scope 语法、registry 协议、drift/coverage 语义），细则演进在该文档版本化，不回填 ADR 正文；
- ADR-0034 本身 = 方向裁决记录（决策、理由、取代关系）；
- `AGENTS.md` 仍为 **minimal bootstrap contract**：保留最少量不可遗漏的启动原则（总原则/硬不变量/按需入口），加一行指向 execution-contract.md——**不空壳化，也不复制执行语义**。

## 3. G2：scoped 上下文文件命名与形态（本 ADR 内裁决）

**现状**：`backend/agent/{,aee/}CLAUDE.md` 内容中立却用 Claude-only 命名；非 Claude Harness 无自动加载（仅根规则人工路由）。

**裁决**：迁移为「scoped `AGENTS.md` 真身（中立内容）+ `CLAUDE.md` 薄壳」：

- **形态优先级：symlink > `@import`**。依据：#857 实证 `@import` 在子目录 cwd 下不解析（-p 与 TUI 双模式）；symlink 在文件系统层生效、与 cwd 无关（deepseek 上游与业界推荐的另一形态，此处获反面实证支撑）。symlink 若被工具链（Windows 协作 / 特定构建）拒绝，退回 `@import` 并以 **#857 修复确认为前置**。
- **试点顺序**：`backend/agent/` → `aee/`；迁移走共享元文件串行 PR。
- **checker 同步**：S6 预算表加 scoped AGENTS.md 条目；S2 `link_files` 加新路径；根层 CLAUDE.md 的 import 形态（S8 已锁）**本次不动**，待 #857 修复后另行评估是否 symlink 化。
- **验收**：四家 Harness 以 cwd=目标目录跑附录 A 探针协议，真身内容全部可见。

## 4. Alternatives（已考虑并否决）

| 备选 | 否决理由 |
|---|---|
| 维持 2026-09-04 约定，不引入 Registry | 多 Harness 常态化后冲突窗口从「同会话」变「跨 Harness」；派生视图只能看本机 diff，跨 worktree 的声明面无载体，「靠避免」不再可审计 |
| P3 建 merge queue | 主干机制已存在（FIFO auto-merge + update-branch + strict）；真增量是 drift/freshness 检测（修正⑥） |
| G2 维持 CLAUDE.md 命名 | 3/3 非 Claude Harness 实测读嵌套 AGENTS.md（附录 A）；维持等于放弃已验证的加载通道 |
| symlink 全局替换（含根层） | 根层 S8 已锁 import 形态且 #857 仅证实子目录缺陷；根层迁移待 #857 修复后独立评估，不随本 ADR 捆绑 |
| auto mode 默认化 / 提高并发上限 | 08-26 synthesis 裁决前提（治理面写者 >1 常态化、auto mode）未满足；并发瓶颈见 §2.6 |
| overlap 仅看 liveness=ACTIVE | finish 后 STALE 的在途变更仍是集成风险窗口（§2.3 反例）；集成窗口与执行者活性是两个正交维度 |
| Phase 1 即引入 heartbeat daemon / TTL 硬语义 | 声明式 CLI 之间无可靠心跳源，硬 TTL 会把「上午 declare、全天编码」的长任务误判（§2.5 分期：P1 advisory，P2 有 heartbeat 后再升格） |

## 5. Verification

- **P0**：建立 `execution-contract.md` 并完成五处最小引用接线；AGENTS.md 在 80 行/8KB 预算内完成 supersede 改写（现 63 行）；治理门禁 S1-S11 全绿；2026-09-04 note 标注 superseded 并交叉链接本文。
- **P1**：`ai_work.py` 自测红绿样例（含 STALE advisory、两维状态、overlap 集合 {NO_PR, PR_OPEN, READY} 分支）；registry root/lock 实测落 `$(git rev-parse --git-common-dir)/ai-work/`；scope MVP 校验（非 repo-relative path 拒绝）；`test_impact` 字段入 schema；`check:quick` 全绿。
- **P2**：cwd 深度 × Harness 加载矩阵（附录 A 协议）全部通过后，Adapter 方可视为就绪；heartbeat 就位后 `last_seen` 升格。
- **P3**：drift gate（含 `coverage-mismatch`）以 advisory 上线，夜间全量含其自测；转 required 须独立裁决。
- **G2 试点**：四 Harness 探针验收 + S6/S2 扩展后门禁绿。
- **#855 收口**：本文合入即满足其主触发条件（ADR-0034 合入完成）；三选一方向（引擎可插拔行为 eval / 每 Harness 确定性摄取自检 / 并入 drift gate 邻接验收）在 P1 实施期裁决——**#857 正是其防范故障类的现实实例**（L0 全绿下的语义传导断裂，仅行为层探针能发现）。

## 6. Revisit

- **G5**（`.agents/` 单家目录 / skills 多消费方）：新增受版本控制的 harness 适配时，按 [`harness-adapters.md`](../development/ai/harness-adapters.md) 修改顺序重估；
- **auto mode 成为默认工作态**：重访行为验证挂载强度（2026-08-26 synthesis 重议条件，现状见 #855）；
- **AGENTS.md 逼近 80 行/8KB ceiling**：预算扩容须独立裁决，不随功能顺手放宽；
- **#857 上游修复**：根层 import 形态与 G2 形态优先级随之复评。

## 附录 A：2026-09-06 Harness 摄取实测矩阵

协议：/tmp 一次性 worktree，`backend/agent/` 放嵌套 `AGENTS.md`（含唯一探针串），各 Harness 以 cwd=该目录非交互启动，单问双题禁用工具（Q1=阳性对照「## 总原则/## 提交前」标题可见性，Q2=探针串可见性）。

| Harness | 嵌套 AGENTS.md 自动发现 | 调用前提（坑） |
|---|---|---|
| Codex 0.153.0 | ✅ live | DeepSeek API 余额；官方文档逐级发现口径一致 |
| Cursor Agent 2026.09.02 | ✅ live | 非交互需 `--trust` |
| OpenCode 1.18.25 | ✅ live | 需本机 `opencode.json`（未跟踪）在启动目录树内 |
| Claude Code 2.1.259 | ❌（子目录通道=CLAUDE.md） | 需显式 `--settings`（alias 对脚本不生效）；`unrecognized_model` 警告无害 |

**延伸矩阵（#857）**：Claude `@AGENTS.md` import 解析——仓库根 ✅ / 子目录 ❌（`-p` 与 TUI 双模式，引文诊断证实字面行未展开、AGENTS.md 五章节零出现；cwd 相对存在同名文件亦不解析）。
