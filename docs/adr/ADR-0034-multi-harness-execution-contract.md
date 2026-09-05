# ADR-0034：多 Harness 并行执行契约与执行登记（Multi-Harness Execution Contract）

- 状态：**Proposed（v0.2 草案，待人工评审）**
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
- `registry.yaml` 落**所有 worktree 之外**（主 checkout 固定绝对路径）且入 `.gitignore`；flock + same-dir temp + fsync + 原子 rename，**仅本地 FS 成立，禁止落 NFS/CIFS**；
- Registry **只暴露 scope 声明、从不上锁**；仅 `ACTIVE` 记录参与 overlap 检测。

### 2.3 状态机（含「等集成」非终态）

```
ACTIVE ──finish(附 PR 号)──▶ ACTIVE(等集成) ──▶ MERGED
   │                              └──────────▶ ABANDONED
```

终态 = **合入或明确放弃**；「编码完成、等集成」是显式中间态——overlap 生命周期终于 merge，非编码结束。

### 2.4 声明与 diff 的关系（diff 优先）

Registry 声明与实际 diff 不一致时**以 diff 为准**；派生视图（对 merge-base 取差异，含未提交）保留为 ground truth，Registry 是补充而非替代。

### 2.5 TTL 与心跳

`status/update` 等命令顺带心跳；TTL 宽松（24h 量级）或以 worktree 活跃度为心跳；超时标 `STALE` **≠ 死**，人工裁决。

### 2.6 并发上限（保留）

显式上限保留 ≈2-3。理由迁移：逐 PR 决策已政策化给 auto-merge（approvals=0 + FIFO），人的注意力从「审 PR」转移到「审审计面」——瓶颈仍是人的吞吐，上限只是换了守的对象。

### 2.7 分期

| 期 | 内容 | 备注 |
|---|---|---|
| P0 | 规则先行：AGENTS.md/CLAUDE.md 改写走**独立 docs PR**（元文件串行化）；规则单一权威源（AGENTS.md/CLAUDE.md/.cursor/.codex/docs 五处 canonical + 最小引用，勿手工镜像）；`harness-adapters.md` 与 Phase -1 基线 note 的并行约定指针接到本文（基线 note Revisit 的既定要求） | 本文 Accepted 后第一个 PR |
| P1 | Registry MVP（ai_work.py + registry.yaml + overlap 检测 + 自测红绿样例） | |
| P2 | Harness Adapter：各 Harness 会话启动时知晓自身 Role——**上下文供给，非路由**（会话由开发者选择启动，Adapter 只保证该会话能读到 Role Context 与共享契约）；**验收含 cwd 深度 × Harness 加载矩阵**（附录 A 协议扩展） | |
| P3 | 真增量 = **Drift / Freshness gate**：先 advisory（本地 run_gates / 夜间全量，守合入路径 ~2min 注意力预算），overlap 粒度用顶层目录作 hint 而非硬门禁 | **不建 merge queue**——主干机制已存在（FIFO enable-auto-merge + update-branch + strict 分支保护） |
| P4 | Integration Planner：仅在「人已难判集成顺序」真实积累后启用 | 观察项 |

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

## 5. Verification

- **P0**：AGENTS.md 在 80 行/8KB 预算内完成 supersede 改写（现 63 行）；治理门禁 S1-S11 全绿；2026-09-04 note 标注 superseded 并交叉链接本文。
- **P1**：`ai_work.py` 自测红绿样例（含 STALE/overlap 分支）；registry 路径确认在 `.gitignore`；`check:quick` 全绿。
- **P2**：cwd 深度 × Harness 加载矩阵（附录 A 协议）全部通过后，Adapter 方可视为就绪。
- **P3**：drift gate 以 advisory 上线，夜间全量含其自测；转 required 须独立裁决。
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
