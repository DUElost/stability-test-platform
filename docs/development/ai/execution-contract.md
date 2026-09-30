# AI Execution Contract（执行契约）

- **状态**：Living v1.16（§3.6 落地 M2 云端实施与对称 PR 前检，Owner 裁决见 #3516）。本文是 Execution Contract 的**唯一权威源**；方向与理由见 [`ADR-0034`](../../adr/ADR-0034-multi-harness-execution-contract.md)，冲突时以本文为准并回溯修订 ADR；历史见[附录 A.4](execution-contract-annex.md#a4-变更历史v11v115自正文头部迁出)。
- **日期**：2026-09-30
- **适用**：协调域内实施者的 Registry 协议与协调域外实施者的 PR 可见性纪律（§3.6）；**Harness 由开发者选择**，不定义路由。
- **上游评审**：两轮八源审查综合 [`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](../../reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)（R1–R30 权威映射）
- **本文演进**：正文承载语义，[规范附录](execution-contract-annex.md)承载实现与历史，两者同版本；冲突以正文为准，细则不回填 ADR。

---

## 1. 术语与数据模型

### 1.1 `lifecycle` 术语消歧

本文的 `lifecycle` 指 **Execution 的执行生命周期**（`CODING/FINISHED/ABANDONED`）。它与 pipeline 域的同名词无关：`pipeline_def.lifecycle` 是 Pipeline 顶层唯一合法键（S11 硬不变量锚定、`pipeline_engine` 域）。实现与文档中如需并提，执行侧写作 `exec.lifecycle`。

### 1.2 持久字段清单（registry.yaml 每条记录）

| 字段 | 说明 |
|---|---|
| `requirement` | 承接的 Requirement 标识（溯源） |
| `harness` | 承接的 Harness（溯源，非指派） |
| `role` | Execution 元数据标签与未来扩展点（执行侧自声明，自由文本，无枚举）：不参与路由、不加语义约束、非文件 ownership 边界（§5.3）。默认且唯一实际运行角色为 `implementation`——declare 缺省即**写入** `implementation`（v1.6 归一化；显式 `--role ""` 同义），历史记录空串同义读取、不迁移；特殊 Role 暂不进入主执行路径，运行时 Role Context 供给为 **deferred capability 而非交付承诺**（v1.5 收敛，ADR-0034 v1.7——原「定义与供给细则归 P2 Adapter」条款自此撤回） |
| `worktree` | worktree 路径 |
| `branch` | worktree 的工作分支（§5.2 第二档 branch diff 的数据源；v1.1 增） |
| `issues` | declared issue 号列表（字符串形态存储；v1.2 增）——`declare --issue N` 可重复显式声明，另从 requirement/branch slug 启发式兜底提取；declare 在窗查重（§3.4）的数据源 |
| `scope` | declared scope（见 §5 语法） |
| `pr_number` | 登记的 PR 号（可空） |
| `lifecycle` | `CODING / FINISHED / ABANDONED`（§3） |
| `last_seen` | 最近一次 Registry 写动作 / 手动 heartbeat 时间（STALE 的派生源；**不承诺在线状态**，liveness 本身不持久化） |
| `created_at` / `updated_at` | 时间戳 |

**不在持久层的**：liveness 值（`LIVE/STALE` 为查询时派生，ADR §2.3）、integration 事实（由 GitHub 权威派生刷新，§3.3——实现可选择缓存最近观测值，但必须带 `observed_at` 且不得作为权威）。

**Role 扩展再开启条件（v1.6 成文，v1.5 收敛 Revisit ②闭环）**：特殊 Role 进入主执行路径仅当出现**声明面消费 Role 的真实需求**——某机制需要按 Role 区分行为（差异化登记纪律、门禁判定、overlap 处理等）——且经用户裁决后以本文新版本 + ADR 增补落地。「多一种标签写法」「想更细的身份标注」**不构成触发**；触发前 registry 接受自由标签值但一律无语义（ADR-0034 v1.7：Role Runtime 为 deferred capability）。

## 2. Registry 协议

### 2.1 工具与发现（平移 ADR §2.2）

- 工具 `tools/dev/ai_work.py`：子命令与选项**以 `--help` 为准**（本文只约束其语义）+ overlap 检测 + declare 在窗 issue 查重（§3.4）；
- **Registry root = `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`**——`--path-format=absolute`（git ≥ 2.31）是**唯一发现方式**：裸 `--git-common-dir` 在主 checkout 返回 cwd 相对路径（仓库根 `.git`、子目录 `../../.git`）、linked worktree 返回绝对路径，行为不一致且裸拼接会算错。不硬编码 `.git`，不提供 common dir 之外的替代落点（防多 Registry 分裂与 NFS/CIFS 落位）；
- 目录内固定两文件：`registry.yaml`（数据）+ `registry.lock`（flock 锁，同目录）；位于 `.git` 内天然不被跟踪；
- **Registry 按克隆隔离**——同一机器多个独立克隆不共享 registry，与派生视图同口径（per-clone），不构成全局登记。

### 2.2 写入协议（v1.12 起细则见附录 A.1）

写入必须**加锁 + 原子替换 + 崩溃可恢复**：任何写命令在「读 → 校验 → 改 → 落盘」全程持 `registry.lock`（flock），经**同目录**临时文件 fsync 后 rename、再 fsync 父目录（文件 fsync 不保证 rename 后目录项的持久性）；校验失败**拒写**（不部分写入）、异常释放锁并清理残留 `.tmp`、`registry.yaml` 解析失败**隔离留证而不自动重建**；Registry 仅限**本地 FS**，禁止 NFS/CIFS。

**九步全序与逐条处置**：见[附录 A.1](execution-contract-annex.md#a1-registry-写入协议细则正文-22-的细则面)（附录是本文的规范组成部分）。

### 2.3 边界（visibility-only）

Registry **不对业务文件/scope 上锁**；`registry.lock` 仅保护 registry 文件自身的原子写。Registry 是补充：actual diff 以 Git 为准（§5.4）、PR 生命周期以 GitHub 为准（§3.3）、验证以 CI 为准。

## 3. 状态模型（三维）与 transition table

### 3.1 三维定义（平移 ADR §2.3）

- **`lifecycle ∈ {CODING, FINISHED, ABANDONED}`**（执行侧自声明）：`CODING`=编码中；`FINISHED`=执行者已停止编码（`finish` 写入，**只写本字段**——与 PR 先后无关；非终态：`resume` 可回退 CODING，T9/#946）；`ABANDONED`=**仅显式人工动作**（`finish --abandon`），永不因超时/命令自动产生，且不可 resume（恢复 = 新 Execution 重新 `declare`）；
- **`liveness ∈ {LIVE, STALE}`**（**永远 advisory、查询时派生、不持久化**）：`STALE` = `now − last_seen > TTL`（TTL 24h 量级），只表示「久未写」。STALE ≠ 死、≠ 可回收、**不退出集成风险窗口**、不影响任何业务语义；
- **`integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}`**（GitHub 权威）：`NO_PR`=未登记 PR；`PR_OPEN`=已登记 PR；`READY`=required checks 全绿（`update` 依 GitHub checks **派生刷新**，非人工宣称；主干推进致 checks 重跑则回退 `PR_OPEN`；不区分 FIFO 队首位置）；`MERGED`/`CLOSED`=终态（合入 / PR 关闭未合），只能由 GitHub PR 状态确认。

### 3.2 overlap（集成风险）真值表

```text
risk = integration ∈ {PR_OPEN, READY}                                ← 开放 PR 恒在窗口（GitHub 事实不被本地 lifecycle 遮蔽）
    OR (integration = NO_PR    AND lifecycle ∈ {CODING, FINISHED})   ← 无 PR 但执行侧未放弃
    OR (integration = CLOSED   AND lifecycle ≠ ABANDONED)            ← PR 被关 ≠ 工作停止（误关/被取代/reopen）
```

- `MERGED` 出局（变更已进主干，风险真实关闭；merge 后继续新工作应重新 `declare`）；
- **缓存失效前置（v1.10）**：`risk = 上式 ∧ ¬landed`（§3.3；无 PR 记录无此语义）；
- `liveness` 不参与；
- **`finish --abandon` 不是「立即出窗」**：无开放 PR 时记录出窗（僵尸出口的唯一合法终点）；有开放 PR（`PR_OPEN/READY`）时**必须警告**「PR 仍在集成窗口」并提示先关闭/转交 PR（转手 = 新 Execution 重新 `declare`），记录留窗直到 GitHub 侧终态；
- **僵尸候选两类（v1.10 与实现对齐）**：① `integration = NO_PR` + `lifecycle ∈ {CODING, FINISHED}` + STALE + **derived 为空** → 提示 `finish --abandon` 收口（原判据「effective scope 为空」在并集语义下不可达；**有开放 PR 的不是僵尸**）；② `closed-unmerged`：`integration = CLOSED` + 同条件 → 仍在风险窗口**并占 issue 槽位**，提示二选一（abandon 出窗 / reopen·转手重新 declare）。判据均为纯函数，不触网、不改状态。

### 3.3 transition table（全组合）

| # | 触发（执行者/命令） | lifecycle | integration | 说明 |
|---|---|---|---|---|
| T1 | `declare` | →CODING | NO_PR | 新记录 |
| T2 | `finish`（无 PR，或带 `--pr N` 且登记新号） | CODING→FINISHED | NO_PR→PR_OPEN（仅当尚为 NO_PR 且带号） | finish 只写 lifecycle；带号时顺带登记 integration |
| T3 | `finish --abandon` 且 integration ∈ {NO_PR, MERGED} | →ABANDONED | 不变 | 出窗（合法终点） |
| T4 | `finish --abandon` 且 integration ∈ {PR_OPEN, READY} | →ABANDONED | 不变 | **警告** PR 仍在窗口；留窗至 T6/T7 |
| T5 | `update`（写命令；顺带刷自身 `last_seen`、现算 derived、drift 检测） | 不变 | PR_OPEN↔READY 派生刷新 | checks 全绿→READY；主干推进重跑→回退 PR_OPEN |
| T6 | `update` 核对 GitHub：PR merged | 不变 | →MERGED | **唯一 MERGED 写入路径**；终态 |
| T7 | `update` 核对 GitHub：PR closed（未合） | 不变 | →CLOSED | 终态；CLOSED 后 risk 由 §3.2 第三行决定 |
| T8 | PR reopen（GitHub 事实） | 不变 | CLOSED→PR_OPEN | `update` 派生刷新 |
| T9 | `resume` | FINISHED→CODING | 不变 | **返工回退（T8 的 lifecycle 侧对称，#946）**：评审意见等要求继续编码时恢复，保审计连续性；`MERGED` 拒绝（风险已真实关闭，走 T1 重新 `declare`）；`ABANDONED` 不可 resume；下一轮 `update` 照常 reconcile integration |

- **并发刷新顺序**：`update` 单次调用内——先 GitHub 核对（T5–T8 的事实采集），再执行 §2.2 九步写入；两次并发 `update` 由 flock 串行；
- **GitHub 不可用降级**：保持旧 integration 值 + 更新 `observed_at`（观测时间），status 输出「integration 观测于 <时间>，GitHub 暂不可达」；**不猜测、不推进终态**；
- **缓存失效 `landed`（v1.10 增，#1232）**：`integration_cache` 只由 `update` 刷新、**可以陈旧**。Git 能证明变更已进主干时该缓存对 risk 判定失效——按 §3.2 出窗、标注 `stale-cache`、提示核销。**本地证据**（零网络）：① `branch`/`origin/<branch>` 是 `origin/main` 的祖先（`branch` 非空且非主干自身）；② `origin/main` 的 GitHub merge 主题含 `#<pr_number>`（覆盖 ref 已删 / 提交改写 / `branch=main`）。**不写 integration 字段**——`MERGED` 唯一写入路径仍是 T6；**fail-safe**：证据取不到即留在窗口，`origin/main` 落后只漏判不误判；
- `status` **严格只读**（不刷任何记录的 `last_seen`——观察不得改变被观察状态）；仅携带 execution identity 的写命令（`declare/update/finish`）刷新**自身** `last_seen`；`status` 不做网络调用（landed 是本地 Git 事实）；
- **`update --all`（v1.11 增，#1234）**：一次 gh 调用取全部 PR 的 `state`，**一次九步写**批量刷新各记录**终态**（MERGED/CLOSED）；**不刷任何 `last_seen`**（无 identity 即不冒充心跳）。PR 仍 OPEN 的记录不在批量侧重算 READY（归单记录 T5）。

### 3.4 declare 在窗 issue 查重（v1.2 增，#978）

多 Harness 并行领单时，同一 issue 可能被不同 requirement 名各自 declare（同名录已在 T1 拒绝，但 `fix-900-a` 与 `fix-900-b` 互不感知）——declare 时按工作项去重：

- **issue 集** = 显式 `--issue N`（可重复）∪ requirement/branch slug 启发式提取（标记 `issue|fix` + 分隔符 + 1-6 位数字；日期串与无标记数字不误报）；
- **拒绝条件**：新 declare 的 issue 集与任何**在窗记录**（§3.2 risk 真值表；MERGED 已出窗不拦）的 issue 集相交 → exit 2，列出冲突记录与 issue 号；
- **`--force`**：仅供人工确认转手/并行边界后显式覆盖，覆盖时输出 `[WARN]` 留痕；同名录拒绝（先 `finish --abandon`）不因 `--force` 放行；
- **空 issue 集必须显性（v1.13 增，#2729）**：issue 集为空时 declare 输出独立一行 `[WARN]`（写明查重对它不生效 + 两种补齐写法），**advisory、不阻断**；行尾 hint 撤除（同一事实只留一处）。实录与取舍见[附录 A.5](execution-contract-annex.md#a5-空-issue-集边界与-2706-撞车实录2729)。
- **定位**：查重是**工作项去重，不是文件上锁**（§2.3 边界不变）——scope overlap（§5.4）管「同一处代码」，issue 查重管「同一件事」，两者互补且都尊重选择权原则（§适用）：冲突由人裁决，工具只保证可见与默认拒绝。

### 3.5 决策实体唯一性与可见性双通道（v1.8 增，#906；v1.14 改双通道）

**事故来源**（#906，2026-09-08）：同一 Requirement 的两个独立 Execution 把结论落成同一编号 ADR-0035 的两份文件，两份均自称权威；实录与裁决见[附录 A.6](execution-contract-annex.md#a6-adr-0035-双权威事故实录906)。

**分层原则**：**Execution ≠ Artifact ≠ Decision**——**一个架构主题在同一时刻只能有一个权威 Decision Artifact**；ADR 是 Decision 的持久化记录、不是 Proposal 的落点。多份独立提案或审计（v1.14 起由协调域外的规划 / 复核工作面组织，§3.6）须经人类裁决后汇聚成一份 ADR。

**可见性双通道**（均只给可见性，不阻断、不预定编号、不宣告所有权）：协调域内修改决策文档的实施 Execution 走 **Registry + §3.4 issue 查重**（纪律 1）；协调域外实施者 / 规划者 / 复核者走**开放 PR 查重**（纪律 2）；两条通道同受纪律 3 约束。

**强制纪律**：

1. **协调域内的决策类 Execution 必须显式 `declare --issue <n>`**（决策类 = 产物为 ADR / 裁决文档 / 设计方向文档）。issue 集是 §3.4 查重的唯一数据源，缺 `--issue` 会让同一 Requirement 的两个 Execution 完全互不感知——本次事故中第二个 Execution 未声明 issue，§3.4 因此静默通过、只剩 hint 级 overlap。声明后同一 issue 的第二次 declare 会被 §3.4 **默认拒绝**，人工确认竞争边界才可 `--force` 放行（放行即留痕）。**v1.10 机械化（#1232）**：scope 声明具体 ADR 文件（`docs/adr/ADR-*`，目录 `docs/adr` 不算）即判为决策类，未带 `--issue` 时 declare **默认拒绝**，人工确认后 `--force` 放行并输出 `[WARN]` 留痕。
2. **落笔前必须扫竞争提案**：未合入的 ADR 提案只在 PR 里可见（`main` 上不存在），故动手写 ADR 前必须检查开放 PR 是否已有**同编号或同主题**的 ADR 文件，并确认目标编号未被占用。协调域内这是 `status` 前检之外的补充；协调域外这是唯一的查重通道。
3. **同一主题的第二份权威 ADR 不得合入**：发现同主题已存在 Accepted/Proposed ADR 时，第二份不得以新编号自行落地为权威，应作为 Proposal 交人类裁决、裁决后合并进既有 ADR（ADR-0035 即此形态）。

**边界**：本条是**可见性与汇聚纪律**，不是调度或上锁——不新增持久字段、不引入 Decision Registry、不改 overlap 谓词（§5.4 仍为 hint 级），也不推翻 §2.3「Registry 不对业务上锁」。协调域内要求决策类 Execution 在声明面说清「我正在形成哪件事的决策」，让 §3.4 查重生效；协调域外由开放 PR 承担同一可见性。

### 3.6 执行模型、协调域与字段封闭性（v1.16，ADR-0058 D10）

- **唯一执行模型**：生产实施 `1 Requirement → 1 Harness → 1 PR`；原 Mode A/B/C 词汇退役（按 Mode C 完成的历史评审不回改）。
- **协调域**：Registry 只登记属于其协调域的实施 Execution——现阶段即宿主机同一克隆的本地 Registry（§2.1）内的 Harness 会话。判据是能否参与该协调域，不是进程在本机还是云端。规划者、复核者、集成观察者（ADR-0058 D8）不属于协调域，不 declare。
- **对称前检**：所有实施者开工前查开放 PR 的目标文件与远端分支；协调域内再 `status --risk` / `declare`。实际 diff 为准，scope 不是 ownership。规划 / 复核工作面写仓库前同样查开放 PR；§3.5 纪律 2、3 同样适用。
- **协调域外实施（M2）**：云端实施者不 declare、不代登记不存在的 worktree；首个可提交的有效改动形成后立即开 draft PR，不制造空提交或伪实现。Owner 对照本地 `status --risk`；可能重叠的本地在窗 Execution 默认不并发，无重叠可并行，共享元文件串行。冲突可见性由开放 PR + Git 承担，PR ready / 复核 / 激活仍遵守 ADR-0058 D8–D9；不是按代码类型划界，也不授予生产写权限。
- **不扩建机制**：不新增 Registry 字段、远程 Registry、标签、CI gate 或自动路由。M2 不把规划 / 复核工作面变成实施者，也不把协调域外实施写入本地 Registry。

**Registry 是 execution coordination metadata，不是 reasoning memory**——§1.2 字段集**封闭**，不得新增 `notes`/`plan`/`reasoning` 类自由文本字段。

## 4. TTL 与 last_seen 语义（v1.15：P2 心跳不实施）

- TTL 仅 advisory：超时只在 status 提示「可能陈旧」并列僵尸候选，不自动改写任何持久字段、不剔除、不降级；
- `last_seen` 只由带 identity 的写命令（`declare/update/finish`）或手动 `heartbeat` 刷新，**不是 liveness 权威**。Adapter 无自动 `whoami` / 定时 heartbeat 义务（退役理由见附录 A.4 v1.15）；`whoami`（只读）与 `heartbeat` / 无参 `update` 保留为手动 / 兼容入口，无真实消费者再退役；
- 协调域外仓库写入（D10-3 当前允许者）的可见性按 §3.6 走开放 PR，不依赖 Registry 心跳；本条不扩展实施者范围。

## 5. effective scope 与 overlap 谓词

### 5.1 并集恒成立

```text
effective_scope = normalized(declared) ∪ derived(diff)
```

- `derived(diff)` 是 Git 事实，声明不能覆盖或删除它；`declared` 无论有无 diff 都保留为意图（Registry 的核心价值 = diff 出现前、以及 diff 未覆盖全部计划范围时的意图可见性）；
- 两者不一致 → **declaration drift 提示**（advisory：列出「声明了但 diff 未体现」「diff 触及但未声明」两清单）——不丢弃声明、不静默放行；
- `update` 可覆写 `declared`（声明过期由执行侧显式清理）。

### 5.2 derived(diff) 三分档

1. worktree 在场 → **工作树 diff**：tracked staged/unstaged（对 merge-base）+ **untracked**（`git ls-files --others --exclude-standard`——`git diff --name-only` 不含新文件，新建文件同样是集成风险）**；归属前提（v1.10 增）**：该 worktree 的 HEAD 提交须等于记录 `branch` 且不被其他在窗记录共用，否则退回第 2 档并标注 `shared-worktree`；
2. worktree 已删除（finish 后常见）→ **branch diff**：`git diff $(git merge-base origin/main <branch>) <branch>`（finish 后本不应有未提交改动，此档不损失信号）；
3. 两者皆不可得（worktree 与分支均不存在）→ 回落到 `declared` 单独生效。

实测反例（为何 drift 提示必须存在）：2026-09-04 `docs/drift-sync-*` 声明 `docs`、实际 diff 触及 `backend/` 与 `.github/`——并集 + drift 提示使两个方向都可见。

### 5.3 scope 语法（MVP，P1 Contract 即约束）

- 表达 = **repo-relative path**，仅 file 或 directory 两种粒度，归一化后存储；
- **明确拒绝**：绝对路径；含 `..` 的路径；仓库内 symlink 逃逸到仓库外；trailing slash 歧义（`dir/` 归一为 `dir`）；
- **不支持**：glob、ownership 语义、自动任务拆分、semantic scope、任何形式的 locking；**Role 不是文件 ownership 边界**。

### 5.4 overlap 谓词（路径组件边界）

两条 effective scope 记录 overlap，当且仅当其中一条的路径组件序列是另一条的前缀（组件级比较，非字符串前缀）：

- `backend` 与 `backend/agent/aee.py` → overlap（目录前缀）；
- `backend` 与 `backend_new/x.py` → **无** overlap（`backend` ≠ `backend_new` 组件）;
- `a.py` 与 `a.py` → overlap（文件全等）；`a.py` 与 `a.py.bak` → 无。

## 6. test_impact 与 coverage-mismatch

- `declare` 附 `test_impact ∈ {none, direct, indirect}`：none=不改行为语义（纯文档/注释）；direct=直接改测试或被测代码；indirect=可能影响行为的非直接改动。**P1 允许缺省**（缺省视同 `indirect` 并在 status 提示）；
- **coverage 评估**（P3 drift gate 家族，advisory）：声明 `none` 但 diff 触及测试相关路径、或声明 `direct` 但无对应测试运行记录 → `coverage-mismatch`；
- **CI 证据口径 = 夜间全量 / 合并后 CI 运行记录**（非 PR 轻量 checks——PR 路径有意不含全量 backend/frontend 测试，按 PR checks 判定会让 `direct` 声明常态误报），与合入路径 ~2min 注意力预算原则联动。

## 7. drift 与强制随附物豁免（v1.12 起清单见附录 A.2）

仓库纪律要求的**强制随附物**不参与 scope drift 比对（属流程义务而非执行意图）；其余一律按 §5 比对。豁免清单见[附录 A.2](execution-contract-annex.md#a2-drift-强制随附物豁免清单正文-7-的清单面)。

## 8. 并发与审计吞吐（v1.7 反转，随 ADR-0034 v1.9）

**会话数不设上限**——原「≈2-3 显式上限」自 2026-09-04 约定未实测继承、被多 Harness 批次 5+ 会话常态超出（含单 Harness 多开），于本版移除（理由与守对象重锚见 ADR §2.6 v1.9）。真实约束在**集成收尾侧**（人的审阅吞吐 + 外部平台可靠性），可观测代理为在窗 Execution（§3.2 risk 集合）规模与 reconcile 负载；「任务排队」仍是主策略（FIFO auto-merge 串行集成、同文件显式串行排程）。Registry 提升的是审计面信息完备性，不是审阅吞吐。

## 9. P1 启动判据与过渡条款（v1.12 起见附录 A.3）

启动判据（三条）与过渡条款**均已满足并收口**：工具已预置就绪并被采用，**现行操作规范就是本文正文协议**，派生视图降为 ground truth 交叉验证手段（§5.4）。判据全文与历史留档见[附录 A.3](execution-contract-annex.md#a3-p1-启动判据与过渡条款已满足历史留档)。

## 10. 演进

本文为 Execution Contract 唯一权威源，按「版本号 + 日期」演进；方向级变更（推翻 ADR-0034 裁决）须先修订 ADR 并走其评审流程，细则级变更直接在本文版本化。ADR §2 细则已迁出（ADR-0034 v1.1），本文与其冲突时以本文为准。

**实现与契约的先后纪律**（v1.4 增）：实现不得静默重新定义 Contract 语义。若实现需要新增、删除或改变项目级可观察语义，应先修改 Contract，再修改实现；不改变项目级可观察语义的纯实现细节，无需修改 Contract。

- **项目级可观察语义**（最小判据）：本契约规定的外部可观察协议行为——registry 持久字段与校验规则（§1.2、§2.2）、三维状态与 transition table（§3）、overlap/issue 查重/drift 判定结果（§3.2、§3.4、§5.1）、CLI 输出与退出码；
- **「先修改 Contract」是语义先行**：契约修订与实现可同 PR 原子落地（#978/#946 先例）；禁止的是实现先于任何契约修订独自合入、或实现合入后契约无版本化收口；
- **与 AGENTS.md 的分工**：本条是**事前过程纪律**（契约先行）；AGENTS.md 总原则「冲突时以代码与测试为准，并同步权威文档」是**事后事实裁决**——后者不豁免前者：发现既成漂移时按 AGENTS.md 同步本文，且同步必须走本节版本化显式收口，不得以「代码已如此」静默追认。
