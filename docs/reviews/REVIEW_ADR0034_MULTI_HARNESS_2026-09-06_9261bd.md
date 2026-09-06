# ADR-0034 v0.3→v0.4 多 Harness 执行契约只读审查

- 审查日期：2026-09-06
- 会话标识：`9261bd`
- 审查对象：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.3）
  - `docs/notes/process/2026-09-06-adr-0034-draft.md`
  - `docs/adr/README.md`
  - Phase -1 Harness 基线、现行并行约定、Harness 适配与仓库集成工作流
- 审查方式：对照用户明确确认的 Contract v1 条款、后续模型校正建议、Git worktree
  实际行为、现有 FIFO auto-merge/CI 事实源和治理门禁进行只读交叉核验
- 审查基线：`main` at `55e9a71e`（包含 PR #860、#861）
- 复审基线：`main` at `1715eee6`（v0.4，包含 PR #862、#863）

> **当前有效结论见 §八。** §一至§七保留 v0.3 审查原貌及人工裁决后的修订痕迹，
> 不再作为 v0.4 的 Accepted 判据。

---

## 一、结论

ADR-0034 v0.3 相比 v0.2 已完成关键方向修正：Registry 移入 Git common dir、固定
`registry.lock`、STALE 不再退出集成窗口、Registry/Git/GitHub/CI 权威分层、Scope
MVP、`test_impact`、单一 Execution Contract 以及复用现有 FIFO auto-merge 均已进入
正文。

但 v0.3 **仍不应转为 Accepted**。当前有 2 项与用户明确确认条款直接冲突的阻断项，
以及 1 项独立于模型维数选择的 `finish` 语义阻断项；另有 4 项应在同一轮修订中收口。
建议发布 v0.3.1，完成本文 B1–B3 与 H1–H4 后再进行 Accepted 评审；在此之前不应启动
P0/P1 实施。

---

## 二、阻断项

### B1. Registry 发现协议没有使用已冻结的绝对 common-dir 形式

ADR §2.2 与 §5 使用：

```text
git rev-parse --git-common-dir
```

冻结版 Contract v1 要求的唯一发现方式是：

```text
git rev-parse --path-format=absolute --git-common-dir
```

本仓库实测：

| cwd | 当前 ADR 命令 | 冻结命令 |
|---|---|---|
| 仓库根 | `.git` | `/home/debian13/stability-test-platform/.git` |
| `backend/agent/` | `../../.git` | `/home/debian13/stability-test-platform/.git` |

当前写法仍依赖调用者 cwd；ADR §2.2 还允许实现选择 common dir 外路径，这与“common-dir
是唯一发现方式”冲突，也重新引入多 Registry 与 NFS/CIFS 落点风险。

**修订要求：**

- 固定使用 `git rev-parse --path-format=absolute --git-common-dir`；
- 明确这是唯一发现方式，不硬编码 `.git`，不提供 common-dir 外替代路径；
- 验收覆盖主 checkout、linked worktree 和深层 cwd，三者必须解析到同一目录。

### B2. 原子写入协议未完整固化

ADR §2.2 只写：

```text
flock registry.lock → same-dir temp + fsync + 原子 rename
```

冻结版 Contract v1 已明确该流程不能留给 P1 实现者自由解释，完整顺序应为：

```text
flock(registry.lock)
→ read registry.yaml
→ validate
→ modify
→ write same-dir registry.yaml.tmp
→ fsync(tmp)
→ rename(tmp, registry.yaml)
→ fsync(parent directory)
→ unlock
```

当前遗漏 `validate`、parent-directory `fsync` 和完整锁覆盖范围。文件 `fsync` 只能保证
文件内容，不能单独保证 rename 后目录项在崩溃后的持久性。

**修订要求：**在 ADR 固化完整硬约束，具体异常类型、残留 temp 清理和损坏恢复再由
`execution-contract.md` 定义。

### B3. `finish` 缺少独立、可持久化的语义表达

对话中存在两版模型：

| 来源 | 模型 |
|---|---|
| 用户明确确认 | Execution state 与 Integration state 两维，核心约束是 STALE 不退出集成风险 |
| 后续审查建议（未获用户显式确认） | lifecycle / liveness / integration 三个正交维度 |

因此，不能把三维模型称为“已冻结 Contract v1”，也不能仅因 v0.3 采用两维模型就判定
其违反冻结裁决。v0.3 当前模型为：

| 维度 | 状态 |
|---|---|
| liveness | `ACTIVE / STALE` |
| integration | `NO_PR / PR_OPEN / READY / MERGED / ABANDONED` |

两维模型的 overlap 结论本身成立：integration window 决定风险集合，STALE 不构成排除
条件。真实阻断点是 §2.3/§2.5 没有为“已停止编码”提供可持久化表达：

1. PR 可以在编码期间已经处于 `PR_OPEN`，此时执行 `finish(PR #N)` 后两个字段都无需
   变化，Registry 无法表达“执行者已停止编码”；
2. `finish` 被写成固定的 `NO_PR → PR_OPEN`，无法覆盖“先开 PR、继续编码、再 finish”
   的正常路径。

**修订要求：**明确记录 coding finished，但不强制三维枚举。可选方案包括：

- 增加独立 lifecycle 字段；或
- 保留两维模型并增加 `finished_at` / `coding_finished` 等明确字段。

无论采用哪种方案，`finish` 都不得自行宣告 GitHub/CI 事实，且不能使记录退出 integration
risk。三维模型是可选设计，不再作为本报告的强制修订结论。

---

## 三、高优先级发现

### H1. Scope MVP 未明确拒绝 `..` 与 symlink escape

§2.8 只要求 repo-relative path 和 normalized storage；§5 只验收“非 repo-relative
path 拒绝”。这不足以约束：

- `backend/../outside` 等含 `..` 输入；
- 绝对路径；
- 仓库内 symlink 指向仓库外；
- file/directory 前缀边界与 trailing slash；
- 不存在但计划新增的路径应如何做 containment 校验。

应在 ADR 明列 `no absolute path / no .. / no symlink escape`，并要求 P0 Contract
定义基于路径组件边界的 overlap 谓词。

### H2. `status` 刷新 `last_seen` 会让观察行为改变被观察状态

§2.5 写明 `status/update` 顺带刷新 `last_seen`。`status` 是读取全局 Registry 的观察
命令；若它刷新记录，则任何巡检都可能把失联 Execution 重新显示为活跃，破坏 stale
判断。

应保持：

- `status` 严格只读；
- 只有带明确 execution identity 的 `update/heartbeat` 可以刷新自身 `last_seen`；
- 读取其他 Execution 不得改变其 liveness。

### H3. STALE 的“持久字段”与“派生提示”互相矛盾

§2.3 称每条记录包含 `liveness ∈ {ACTIVE, STALE}`，且超时“标 STALE”；§2.5 又规定
P1 超时“不自动改写任何字段”，只在输出中提示可能陈旧。实现者无法判断 P1 的 STALE
究竟是：

- 持久化枚举值；
- 由 `now - last_seen` 派生的显示状态；
- 只能人工写入的字段。

建议拆为：

- 持久化 `last_seen`；
- P1 `effective_liveness` 仅派生显示，不回写；
- P2 heartbeat 就位后仍由查询时派生，或明确唯一回写者；
- 不论采用哪种形式，都不得影响 integration risk。

### H4. 单一权威源与“五处 canonical”表述冲突

§2.10 正确声明 `execution-contract.md` 是唯一权威源，但 §2.7 P0 又写
“AGENTS.md/CLAUDE.md/.cursor/.codex/docs 五处 canonical”。这既像五份权威副本，
又可能被解析为不存在的 `.codex/docs` 路径。

应改为“一个 canonical Contract + 明确列举的薄入口”，并在 P0 验收中列出准确文件，
禁止以目录名或“五处 canonical”概括。

---

## 四、中等与文档一致性问题

### M1. `READY` 的定义与现有 FIFO 行为不够精确

§2.3 把 READY 定义为“required checks 全绿、进入 FIFO 集成位”。现有实现中，绿灯 PR
可以处于队列非队首，只有队首会启用 auto-merge。需明确 READY 表示：

- checks 通过且 eligible；还是
- 已成为队首；还是
- 已启用 auto-merge。

READY 由 CI/队列事实派生，不能由 Registry 自声明；若主干推进导致 checks 重新运行，
还需定义 `READY → PR_OPEN` 的回退。

### M2. Integration transition 与 reconciliation 入口不完整

当前 CLI 只有 `declare/status/update/finish`，但没有明确：

- `PR_OPEN → READY` 的事实采集者；
- `READY → MERGED` 的 GitHub reconciliation；
- PR closed without merge 如何映射；
- 谁可以写 `ABANDONED/CLOSED`；
- GitHub API 不可用时是否保持旧状态并标记观测时间。

这些可以下沉到 P0 `execution-contract.md`，但 ADR 应将 transition table、reconcile
来源和缓存 provenance 列为 P0 必备目录，防止 P1 先于语义落地。

### M3. G2 尚未解决深层 Claude 对根启动契约的摄取

§3 的 scoped `AGENTS.md` 真身 + `CLAUDE.md` symlink 可以让 Claude 读取 scoped 内容，
但 #857 已证明深层 cwd 下根 `CLAUDE.md` 的 `@AGENTS.md` 不展开。根层形态本次又明确
不动，因此 scoped symlink 本身不能保证 Claude 同时获得根 AGENTS 的总原则与硬不变量。

附录 A 的 Q1 正是根标题阳性对照，当前 Claude 仍会失败。P2 必须明确根契约供给方案，
并将“根 bootstrap + scoped 内容”同时可见作为验收，而不能只写“真身内容全部可见”。

### M4. ADR 索引和起草记录仍有漂移

- `docs/adr/README.md` 的 M7 看板已更新为 v0.3，但“当前 ADR 清单”仍止于 ADR-0033；
- `docs/DOC-MAP.md` 尚无 ADR-0034；
- 起草 Note 第 8 行仍称 Proposed v0.1；
- 起草 Note 第 10 行仍保留 v0.1/v0.2 的“worktree 外 + gitignore”摘要，虽然后文新增
  v0.3 修订记录，但当前状态不够直接；
- `docs/design/2026-08-governance-surface-protection.md` 汇总表仍写 S1–S10，实际已经
  是 S1–S11。

这些不阻断方向裁决，但应在 v0.3.1/P0 文档 PR 一并校正。

### M5. #855 的“合入触发”仍有语义歧义

ADR 已合入 Git，但仍为 Proposed，P0/P1 尚未发生。§5 把“本文合入”写成 #855 主触发，
而相关 Issue 评论又以 Accepted 后启动为实际节奏。建议明确区分：

- Git merge：草案可被引用；
- ADR Accepted：方向生效；
- P0 Contract 完成：行为验证方案可开始实施。

### M6. Competition 是取代背景，但不是已冻结的执行条款

用户原方案与第 9 点确认中确实使用了 `competition mode` / “显式 Competition”，并将
competition 列为 Phase 4 的真实使用观察对象。这足以说明 Competition 是新机制取代
“冲突靠避免”时的背景和预期场景之一。

但现有对话没有冻结 Competition 的字段、CLI、审批、结束或候选淘汰协议。“显式受审计
竞争”是本报告根据 Registry 可审计性作出的归纳，不是用户确认过的规范原文。因此 ADR
未定义 Competition 不构成 Accepted 阻断项；可在取代理由或 P4 Revisit 中补一行以保留
追溯性，具体机制等出现真实需求后再裁决。

---

## 五、已闭环项

| v0.2 审计问题 | v0.3 结果 |
|---|---|
| Registry 位于主 checkout，跨 worktree 可能分裂 | 已改为 Git common dir，方向正确 |
| 锁数据文件而非固定锁 | 已固定 sibling `registry.lock` |
| 仅 ACTIVE 参与 overlap | 已改为 integration window，liveness 不参与 |
| STALE 自动退出风险集合 | 已明确永不退出 |
| P1 无可靠 heartbeat | 已分期，P1 advisory、P2 wrapper heartbeat |
| Registry 复制 GitHub/CI 事实 | 已建立 Registry/Git/GitHub/CI 权威表 |
| `finish` 可自行宣告 MERGED | 已禁止，MERGED 必须核对 GitHub |
| Scope 支持面过宽 | 已限制 file/directory，拒绝 glob/ownership/locking |
| 缺少 Test Impact | 已加入 `test_impact` 与 coverage-mismatch advisory |
| 缺少单一完整契约源 | 已把 `execution-contract.md` 定为 P0 唯一权威源 |
| 重建 Merge Queue | 已明确复用现有 FIFO auto-merge |
| Worktree/Role 归属混淆 | Worktree 属于 Execution，Role 不构成 ownership |

---

## 六、验证证据

本次只读审查实际执行：

```text
venv/bin/python tools/dev/check_governance_surface.py --check
venv/bin/python tools/dev/check_governance_surface.py --self-test
venv/bin/python scripts/run_gates.py check:quick
git diff --check
git status --short --branch
git rev-parse --git-common-dir
git -C backend/agent rev-parse --git-common-dir
git rev-parse --path-format=absolute --git-common-dir
git -C backend/agent rev-parse --path-format=absolute --git-common-dir
```

结果：

- governance check：S1–S11、S5x 全绿；
- governance self-test：12 条规则红/绿样例全绿；
- `check:quick`：ruff、eslint、tsc、knip、compileall、gov-surface 全绿；
- PR #860 与 #861 的 required checks 全部通过；
- 审查开始前工作树仅包含其他会话生成的未跟踪 review 文件，本报告未修改它们。

上述结果证明当前仓库结构和静态质量门禁健康，但不证明 ADR 的状态语义已经闭合。

---

## 七、建议的 v0.3.1 最小修订集

1. Registry root 固定为 absolute git-common-dir，删除替代落点；
2. 补全原子写入九步协议；
3. 为 `finish` 增加独立、可持久化的 coding-finished 表达；可采用 lifecycle 或
   `finished_at`，不强制三维枚举；
4. 明列 Scope 的 absolute/`..`/symlink escape 拒绝规则；
5. 让 `status` 严格只读，澄清 STALE 派生/持久化模型；
6. 明确 READY、MERGED、CLOSED/ABANDONED 的权威 transition 与 reconciliation；
7. 将“五处 canonical”改为“单一 Contract + 精确薄入口清单”；
8. 修正 G2 根契约摄取验收与索引/版本漂移；
9. 可选：在取代理由或 P4 Revisit 中记录 Competition 场景，不提前冻结实现协议。

完成上述修订后，可再次进行短周期只读复审；复审通过后再将 ADR-0034 改为 Accepted，
随后执行 P0 Contract 与入口接线，最后进入 P1 Registry MVP。

---

## 八、v0.4 复审（当前有效结论）

### 8.1 总评

v0.4 已闭环 v0.3 的 B1–B3、H1–H4 及大部分文档一致性问题：

- Registry root 固定为
  `git rev-parse --path-format=absolute --git-common-dir` 的唯一发现方式；
- 原子写入补齐九步全序与 parent-directory `fsync`；
- lifecycle 为 `finish` 提供独立持久化表达，并明确三维是 ADR 的实现选择而非冻结条款；
- STALE 改为由 `last_seen` 查询时派生，`status` 严格只读；
- READY/MERGED/CLOSED 绑定 GitHub 权威，`finish` 不再冒充外部事实；
- Scope 补齐 absolute/`..`/symlink escape/组件边界约束；
- 单一 Contract、薄入口、G2 根契约双边验收及 #855 三段触发均已写入。

但状态组合与 effective scope 仍各有一处会导致不兼容或不安全实现的缺口。v0.4
应继续保持 Proposed，完成以下 B4–B5 后可转 Accepted。

### 8.2 新发现的阻断项

#### B4. `ABANDONED` 可错误释放仍开放的 PR

v0.4 定义：

```text
lifecycle ∈ {CODING, FINISHED, ABANDONED}
integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}
overlap = lifecycle != ABANDONED
          AND integration ∈ {NO_PR, PR_OPEN, READY}
```

同时提供 `finish --abandon`，但没有限制该动作只能作用于无 PR 或已经 CLOSED 的记录。
因此以下合法可构造组合会被排除：

```text
ABANDONED + PR_OPEN
ABANDONED + READY
```

GitHub 上 PR 仍开放、仍可能进入 FIFO 集成，但 Registry 已不再提示 overlap。这违反
“开放 PR 的集成风险不因执行者退出而消失”的核心原则。

另一个同类组合是 `CODING + CLOSED`：若 PR 被关闭但 Harness 仍在编码，当前公式也会
因 integration 已终态而释放风险。

**修订要求：**

1. `PR_OPEN/READY` 必须始终参与 integration risk，不得被 lifecycle=ABANDONED 覆盖；
2. `finish --abandon` 若已登记开放 PR，应拒绝执行，或要求先由 GitHub 确认 CLOSED；
3. P0 transition table 必须列出允许/禁止的组合及并发刷新顺序；
4. 推荐以真值表定义风险，而不是简单对两个维度做 AND。例如：

```text
risk =
    integration ∈ {PR_OPEN, READY}
    OR lifecycle = CODING
    OR (lifecycle = FINISHED AND integration = NO_PR)
```

具体公式可以调整，但必须保证开放 PR 永不被本地 lifecycle 状态遮蔽。

#### B5. effective scope 同时存在两套互斥定义

§2.2 同一条先定义：

```text
effective_scope = declared ∪ derived(diff)
```

随后又写：

```text
声明仅在 derived(diff) 为空时单独生效
Registry 从不凭声明单独判定 overlap
```

若 `derived(diff)` 非空但与声明不同，实现者无法判断：

- 保留并集，同时提示 declaration drift；还是
- 丢弃 declared，只使用 derived。

前者才能保留“尚未触碰但计划修改”的意图；后者会在第一次产生 diff 后使剩余声明路径
从风险面消失。Registry 的核心新增价值恰是 diff 出现前和 diff 尚未覆盖全部计划范围时
提供意图可见性，因此这里不能留给实现自由解释。

**修订要求：**

```text
effective_scope = normalized(declared) ∪ derived(diff)
```

- derived 永远作为 Git 事实存在，声明不能覆盖或删除它；
- declared 在有无 diff 时都保留为意图；
- 两者不一致时输出 drift，但 overlap 对 advisory 并集计算；
- `derived(diff)` 必须明确包含 tracked staged/unstaged 与 untracked 文件。当前
  `repository-workflow.md` 的 `git diff --name-only` 不含 untracked，新建文件不能被称为
  ground truth；P0 Contract 应补 `git ls-files --others --exclude-standard` 等价口径。

### 8.3 非阻断同步项

1. `docs/adr/README.md` 主表已写 v0.4，但 M7 看板仍写 Proposed v0.3；
2. README/DOC-MAP 的“三维状态模型”宜补充“ADR 实现选择”，避免再次被误读为冻结条款；
3. 本报告 v0.3 结论已由本节显式标记为历史；其他审查快照应通过 synthesis 阅读，不宜
   单独作为当前 gate；
4. §3 “symlink 天然防误写”表述不准确：经 `CLAUDE.md` symlink 写入会直接修改目标
   `AGENTS.md`。symlink 消除的是双份内容漂移，不提供写保护；写路径限制需要 checker、
   hook 或明确操作规则；
5. Appendix A 仍只有四家 Harness，未包含 Phase -1 标记为未安装的 Antigravity CLI。
   应明确 Antigravity 为未验证/延期，而不是静默从验收矩阵消失；
6. P0 Contract 应说明 P1 启动条件未触发时继续使用派生视图，避免旧 note 被 supersede
   后出现无现行操作规范的过渡窗口。

### 8.4 v0.4 验证

本次复审实际执行：

```text
venv/bin/python tools/dev/check_governance_surface.py --check
venv/bin/python tools/dev/check_governance_surface.py --self-test
venv/bin/python scripts/run_gates.py check:quick
git diff --check
git status --short --branch
```

并核验：

- PR #862、#863 required checks 全绿；
- ADR、起草 Note、synthesis 与 ADR README 的相对链接存在；
- DOC-MAP 已登记 ADR-0034 v0.4；
- governance 汇总已从 S1–S10 更新为 S1–S11；
- 工作树在复审开始时干净。

这些结果证明文档结构、静态质量与既有门禁健康，但不会覆盖 B4/B5 的组合状态语义。

### 8.5 v0.4 Accepted 判据

建议在同一修订中：

1. 修正 `ABANDONED`/开放 PR 的风险真值表与 transition guard；
2. 将 effective scope 唯一化为 declared 与完整 derived diff 的 advisory 并集；
3. 同步 README M7 版本与 symlink 写保护表述。

完成前两项后，ADR-0034 的方向与核心状态语义即可转为 Accepted；P0
`execution-contract.md`、入口接线、G2 试点和 P1 Registry 实现仍按 ADR 分期后置。

