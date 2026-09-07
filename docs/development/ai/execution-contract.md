# AI Execution Contract（执行契约）

- **状态**：Living v1.7（本文是 Execution Contract 的**唯一权威源**；方向裁决与理由见 [`ADR-0034`](../../adr/ADR-0034-multi-harness-execution-contract.md)（Accepted），两者冲突时以本文为准并回溯修订 ADR。v1.7 变更：§8 并发上限反转——移除「≈2-3 显式上限」，会话数不设上限，瓶颈校准为集成收尾侧、守对象重锚为在窗 Execution 规模与 reconcile 负载（用户 2026-09-08 裁决，ADR-0034 v1.9）；v1.6 变更：§1.2 role 缺省归一化——declare 缺省写入 `implementation`（历史空串同义读取不迁移）+ 定义 Role 扩展再开启条件（v1.5 收敛 Revisit 两项闭环，ADR-0034 v1.8）；v1.5 变更：§1.2 `role` 语义收敛——Role=保留元数据与未来扩展点、默认 `implementation`、Role Runtime 供给降级 deferred（用户 2026-09-08 裁决，ADR-0034 v1.7）；v1.4 变更：§10 增「实现与契约的先后纪律」——实现不得静默重新定义 Contract 语义（用户 2026-09-08 确认）；v1.3 变更：§2.1/§3.1/§3.3 增 T9 `resume`——FINISHED→CODING 返工回退（#946）；v1.2 变更：§1.2 增 `issues` 持久字段、§2.1/§3.4 增 declare 在窗 issue 查重（#978）；v1.1 变更：§9 启动判据增补「已计划的多 Harness 批次启动前预置就绪」（用户 2026-09-07 裁决）；§1.2 增 `branch` 持久字段）- **日期**：2026-09-08
- **适用**：所有在本仓库参与 Execution Registry 的 AI Coding Harness 会话；**用哪个 Harness 承接哪个 Requirement 始终由开发者决定**（选择权原则，ADR §2.1）——本文只约束已被选择的 Execution 如何登记与协同可见，不定义任何路由或自动下发
- **上游评审**：两轮八源审查综合 [`REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`](../../reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)（R1–R30 权威映射）
- **本文演进**：版本化演进于本文；细则不再回填 ADR 正文（ADR-0034 升 v1.1 收缩为决策要点 + 指针）

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
| `last_seen` | 最近写命令时间（liveness 派生源；**liveness 本身不持久化**） |
| `created_at` / `updated_at` | 时间戳 |

**不在持久层的**：liveness 值（`LIVE/STALE` 为查询时派生，ADR §2.3）、integration 事实（由 GitHub 权威派生刷新，§3.3——实现可选择缓存最近观测值，但必须带 `observed_at` 且不得作为权威）。

**Role 扩展再开启条件（v1.6 成文，v1.5 收敛 Revisit ②闭环）**：特殊 Role 进入主执行路径仅当出现**声明面消费 Role 的真实需求**——某机制需要按 Role 区分行为（差异化登记纪律、门禁判定、overlap 处理等）——且经用户裁决后以本文新版本 + ADR 增补落地。「多一种标签写法」「想更细的身份标注」**不构成触发**；触发前 registry 接受自由标签值但一律无语义（ADR-0034 v1.7：Role Runtime 为 deferred capability）。

## 2. Registry 协议

### 2.1 工具与发现（平移 ADR §2.2）

- 工具 `tools/dev/ai_work.py`：`declare / status / update / finish`（含 `finish --abandon`）/ `resume` 子命令 + overlap 检测 + declare 在窗 issue 查重（§3.4，#978）；
- **Registry root = `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`**——`--path-format=absolute`（git ≥ 2.31）是**唯一发现方式**：裸 `--git-common-dir` 在主 checkout 返回 cwd 相对路径（仓库根 `.git`、子目录 `../../.git`）、linked worktree 返回绝对路径，行为不一致且裸拼接会算错。不硬编码 `.git`，不提供 common dir 之外的替代落点（防多 Registry 分裂与 NFS/CIFS 落位）；
- 目录内固定两文件：`registry.yaml`（数据）+ `registry.lock`（flock 锁，同目录）；位于 `.git` 内天然不被跟踪；
- **Registry 按克隆隔离**——同一机器多个独立克隆不共享 registry，与派生视图同口径（per-clone），不构成全局登记。

### 2.2 写入协议（九步全序，硬约束）

```text
flock(registry.lock)
→ read registry.yaml
→ validate          ← schema 校验：必填字段/枚举值/scope 语法（§5），非法即拒绝写入
→ modify
→ write same-dir registry.yaml.tmp
→ fsync(tmp)
→ rename(tmp, registry.yaml)
→ fsync(parent dir)   ← 文件 fsync 不保证 rename 后目录项的崩溃持久性
→ unlock
```

- **异常处置**：`validate` 失败 → 拒写并原样报错（不部分写入）；`fsync`/`rename` 失败 → 释放锁、删除残留 `.tmp`、报错退出（记录保持旧值）；
- **残留 tmp 清理**：任何命令启动时（持锁后）发现无主 `registry.yaml.tmp`（mtime 早于当前进程启动）即删除；
- **损坏恢复**：`registry.yaml` 解析失败时**不自动重建**——重命名为 `registry.yaml.corrupt-<timestamp>` 留证并报错，由人工决定重建（Registry 是声明面，丢了可重 declare，静默清空会伪造「无人工作」）；
- **仅本地 FS 成立，禁止落 NFS/CIFS**（flock 语义与原子 rename 不保证）。

### 2.3 边界（visibility-only）

Registry **不对业务文件/scope 上锁**；`registry.lock` 仅保护 registry 文件自身的原子写。Registry 是补充：actual diff 以 Git 为准（§5.4）、PR 生命周期以 GitHub 为准（§3.3）、验证以 CI 为准。

## 3. 状态模型（三维）与 transition table

### 3.1 三维定义（平移 ADR §2.3）

- **`lifecycle ∈ {CODING, FINISHED, ABANDONED}`**（执行侧自声明）：`CODING`=编码中；`FINISHED`=执行者已停止编码（`finish` 写入，**只写本字段**——与 PR 先后无关；非终态：`resume` 可回退 CODING，T9/#946）；`ABANDONED`=**仅显式人工动作**（`finish --abandon`），永不因超时/命令自动产生，且不可 resume（恢复 = 新 Execution 重新 `declare`）；
- **`liveness ∈ {LIVE, STALE}`**（**永远 advisory、查询时派生、不持久化**）：`STALE` = `now − last_seen > TTL`（TTL 24h 量级）。STALE ≠ 死、≠ 可回收、**不退出集成风险窗口**、不影响任何业务语义；
- **`integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}`**（GitHub 权威）：`NO_PR`=未登记 PR；`PR_OPEN`=已登记 PR；`READY`=required checks 全绿（`update` 依 GitHub checks **派生刷新**，非人工宣称；主干推进致 checks 重跑则回退 `PR_OPEN`；不区分 FIFO 队首位置）；`MERGED`/`CLOSED`=终态（合入 / PR 关闭未合），只能由 GitHub PR 状态确认。

### 3.2 overlap（集成风险）真值表

```text
risk = integration ∈ {PR_OPEN, READY}                                ← 开放 PR 恒在窗口（GitHub 事实不被本地 lifecycle 遮蔽）
    OR (integration = NO_PR    AND lifecycle ∈ {CODING, FINISHED})   ← 无 PR 但执行侧未放弃
    OR (integration = CLOSED   AND lifecycle ≠ ABANDONED)            ← PR 被关 ≠ 工作停止（误关/被取代/reopen）
```

- `MERGED` 出局（变更已进主干，风险真实关闭；merge 后继续新工作应重新 `declare`）；
- `liveness` 不参与；
- **`finish --abandon` 不是「立即出窗」**：无开放 PR 时记录出窗（僵尸出口的唯一合法终点）；有开放 PR（`PR_OPEN/READY`）时**必须警告**「PR 仍在集成窗口」并提示先关闭/转交 PR（转手 = 新 Execution 重新 `declare`），记录留窗直到 GitHub 侧终态；
- 僵尸候选清单：`status` 输出「`lifecycle ∈ {CODING, FINISHED}` 且 STALE 且 effective scope 为空」的记录，人工经 `finish --abandon` 收口。

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
- `status` **严格只读**（不刷任何记录的 `last_seen`——观察不得改变被观察状态）；仅携带 execution identity 的写命令（`declare/update/finish`）刷新**自身** `last_seen`。

### 3.4 declare 在窗 issue 查重（v1.2 增，#978）

多 Harness 并行领单时，同一 issue 可能被不同 requirement 名各自 declare（同名录已在 T1 拒绝，但 `fix-900-a` 与 `fix-900-b` 互不感知）——declare 时按工作项去重：

- **issue 集** = 显式 `--issue N`（可重复）∪ requirement/branch slug 启发式提取（标记 `issue|fix` + 分隔符 + 1-6 位数字；日期串与无标记数字不误报）；
- **拒绝条件**：新 declare 的 issue 集与任何**在窗记录**（§3.2 risk 真值表；MERGED 已出窗不拦）的 issue 集相交 → exit 2，列出冲突记录与 issue 号；
- **`--force`**：仅供人工确认转手/并行边界后显式覆盖，覆盖时输出 `[WARN]` 留痕；同名录拒绝（先 `finish --abandon`）不因 `--force` 放行；
- **定位**：查重是**工作项去重，不是文件上锁**（§2.3 边界不变）——scope overlap（§5.4）管「同一处代码」，issue 查重管「同一件事」，两者互补且都尊重选择权原则（§适用）：冲突由人裁决，工具只保证可见与默认拒绝。

## 4. TTL 与心跳分期

- **P1（无 heartbeat daemon）**：TTL 仅 advisory——超时只在 status 提示「可能陈旧」并列僵尸候选，不自动改写任何持久字段、不剔除、不降级。声明式 CLI 之间没有可靠心跳源，此期 `last_seen` 不是 liveness 权威；
- **P2（Harness wrapper/adapter 提供 heartbeat）**：`last_seen` 升格为可靠 liveness 信号；STALE 仍为派生展示，advisory 语义不变。

## 5. effective scope 与 overlap 谓词

### 5.1 并集恒成立

```text
effective_scope = normalized(declared) ∪ derived(diff)
```

- `derived(diff)` 是 Git 事实，声明不能覆盖或删除它；`declared` 无论有无 diff 都保留为意图（Registry 的核心价值 = diff 出现前、以及 diff 未覆盖全部计划范围时的意图可见性）；
- 两者不一致 → **declaration drift 提示**（advisory：列出「声明了但 diff 未体现」「diff 触及但未声明」两清单）——不丢弃声明、不静默放行；
- `update` 可覆写 `declared`（声明过期由执行侧显式清理）。

### 5.2 derived(diff) 三分档

1. worktree 在场 → **工作树 diff**：tracked staged/unstaged（对 merge-base）+ **untracked**（`git ls-files --others --exclude-standard`——`git diff --name-only` 不含新文件，新建文件同样是集成风险）；
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

## 7. drift 与强制随附物豁免

仓库纪律要求的强制随附物不参与 scope drift 比对（属流程义务而非执行意图）：`docs/notes/` 下的 Agent Note（含本仓库「非平凡变更必须附 Note」要求产生的文件）；`.github/workflows/*.yml` 中由 required checks 演进触发的配套改动（属门禁接线，须在 PR 描述中显式提及，豁免仅限 drift 提示、不豁免评审）。其余一律按 §5 比对。

## 8. 并发与审计吞吐（v1.7 反转，随 ADR-0034 v1.9）

**会话数不设上限**——原「≈2-3 显式上限」自 2026-09-04 约定未实测继承、被多 Harness 批次 5+ 会话常态超出（含单 Harness 多开），于本版移除（理由与守对象重锚见 ADR §2.6 v1.9）。真实约束在**集成收尾侧**（人的审阅吞吐 + 外部平台可靠性），可观测代理为在窗 Execution（§3.2 risk 集合）规模与 reconcile 负载；「任务排队」仍是主策略（FIFO auto-merge 串行集成、同文件显式串行排程）。Registry 提升的是审计面信息完备性，不是审阅吞吐。

## 9. P1 启动判据与过渡条款

- **启动判据**（满足其一即启动，**v1.1 修订**）：
  1. **已计划的多 Harness 工作批次启动前**（2026-09-07 用户裁决增补——ADR-0034 的立项背景本就是「即将开展多 Issue 集中修复与新需求开发的多 Harness AI Coding」，工具须**在批次开始前预置就绪**，而非等场景自然发生；原「等撞车」判据把因果倒置）；
  2. 连续两周并行 worktree ≥3（数据源 = git worktree 历史/日志统计，与派生视图同源）；
  3. 实际发生 ≥2 次跨 Harness 撞车返工。
- **过渡条款**：`ai_work.py` 就绪并被采用之前，**维持 2026-09-04 约定的派生视图用法**（`git worktree list` 遍历 + 对 merge-base 取差异）作为现行操作规范——防止「旧规范已废、新工具未启」的空窗；工具就绪后派生视图仍保留为 ground truth 交叉验证手段（§5.4）。

## 10. 演进

本文为 Execution Contract 唯一权威源，按「版本号 + 日期」演进；方向级变更（推翻 ADR-0034 裁决）须先修订 ADR 并走其评审流程，细则级变更直接在本文版本化。ADR §2 细则已迁出（ADR-0034 v1.1），本文与其冲突时以本文为准。

**实现与契约的先后纪律**（v1.4 增）：实现不得静默重新定义 Contract 语义。若实现需要新增、删除或改变项目级可观察语义，应先修改 Contract，再修改实现；不改变项目级可观察语义的纯实现细节，无需修改 Contract。

- **项目级可观察语义**（最小判据）：本契约规定的外部可观察协议行为——registry 持久字段与校验规则（§1.2、§2.2）、三维状态与 transition table（§3）、overlap/issue 查重/drift 判定结果（§3.2、§3.4、§5.1）、CLI 输出与退出码；
- **「先修改 Contract」是语义先行**：契约修订与实现可同 PR 原子落地（#978/#946 先例）；禁止的是实现先于任何契约修订独自合入、或实现合入后契约无版本化收口；
- **与 AGENTS.md 的分工**：本条是**事前过程纪律**（契约先行）；AGENTS.md 总原则「冲突时以代码与测试为准，并同步权威文档」是**事后事实裁决**——后者不豁免前者：发现既成漂移时按 AGENTS.md 同步本文，且同步必须走本节版本化显式收口，不得以「代码已如此」静默追认。
