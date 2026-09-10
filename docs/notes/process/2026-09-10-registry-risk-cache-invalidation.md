# Registry 风险集合缓存失效 + derived 归属 + 执行模式词汇表（#1232）

Status: implemented
Class: process

## Decision

**契约 v1.10 + 实现同 PR**（§10 语义先行），四项：

1. **integration 缓存失效 `landed`（契约 §3.2/§3.3）**：`risk = §3.2 真值表 ∧ ¬landed`。
   `landed` = Git 证明「本记录分支的变更已进主干」，两条**本地**证据（零网络）：
   ① `branch`/`origin/<branch>` 是 `origin/main` 的祖先（`branch` 须非空且非主干自身）；
   ② `origin/main` 的 GitHub merge 主题含 `#<pr_number>`（覆盖 ref 已删除、提交被改写、
   `branch=main` 三种形态）。命中即按 `MERGED` 的**同一理由**出窗，`status`/`drift` 标注
   `stale-cache` 并提示 `update` 核销。**不写 `integration` 字段**——`MERGED` 唯一写入路径
   仍是 T6（GitHub 权威分层不变）。判据 **fail-safe**：证据取不到即视为未 landed、留在窗口，
   `origin/main` 落后只漏判不误判。
2. **derived 归属前提（契约 §5.2）**：档 1（worktree diff）要求 worktree 的 HEAD 提交等于
   记录 `branch`，且该 worktree 未被其他在窗记录共用；任一不成立退回档 2（branch diff）
   并标注 `shared-worktree`。
3. **僵尸候选与实现对齐（契约 §3.2）**：判据文案改回实现口径（STALE + **derived 为空**，
   原「effective scope 为空」在 §5.1 并集语义下不可达），并显式限定 `NO_PR`——有开放 PR 的
   记录不是僵尸（其工作在 PR 里；对它提示 `finish --abandon` 与 T4 自相矛盾）。两类僵尸
   候选（`NO_PR` / `closed-unmerged`）合并为一条，互不重叠。
4. **执行模式词汇表与字段封闭性（契约 §3.6 新节）+ 决策类判据机械化（§3.5）**：
   Mode A 生产执行 / Mode B 竞争探索 / Mode C 并行审计；共性 = **先独立、后汇聚**，
   Registry 是 execution coordination metadata、不是 reasoning memory，§1.2 字段集封闭
   （禁止 `notes`/`plan`/`reasoning`/`solution` 类自由文本字段）。§3.5 纪律①机械化为
   **scope 声明具体 `docs/adr/ADR-*` 文件即判为决策类**，未带 `--issue` 时 declare 默认
   拒绝、`--force` 放行留痕——只用既有 scope 数据，不新增字段、不预定编号。

**事故/缺陷证据（2026-09-10 00:28 快照，registry 123 条 / 6 家 harness）**：在窗记录中
有 PR 号者 86%（25/29）cache 称开放而 GitHub 已终态；44 条在窗时 321 条 overlap-hint 中
**319 条**涉及已合入记录（仅 2 条是两条真开着的记录），**25 条** `[zombie-candidate]`
**全部**打在已合入记录上并建议 `finish --abandon`；issue 查重被 23 个已合入记录造成误占
（例 `--issue 906`）。另实测 3 条在窗记录共用主检出，`derived_paths` 只看目录存在，
三条记录因此拿到**同一个**别人的未跟踪文件（其中一条的 branch 与 worktree HEAD 不一致，
其真实工作对 registry 完全不可见）。

## Alternatives

- **再抬 S6 预算**（26000→27000）：v1.9 的 Revisit 已定「再次逼近 26KB 应优先契约分层而非
  继续抬预算」；本版改为**压缩自己新增的文本**（证据与理由移入本 Note），净增 2381 bytes
  在预算内（25977/26000）。**不压缩无关章节**——违背 AGENTS.md「只改当前 Requirement
  必需内容」。
- **由 Git 直接写 `integration = MERGED`**：会颠覆 §3.3 事实来源分层（`MERGED` 只由 T6/
  GitHub 确认）与 T6「唯一写入路径」条款；改为「缓存值对 risk 判定失效」——语义等价于
  MERGED 的**风险理由**，权威位不动。
- **只做批量 reconcile（P1），不改判据**：把正确性继续挂在「人记得跑 update」上；实测
  reconcile 滞后中位 27.4h（最长 38.7h），而 `status` 是 AGENTS.md 的强制前检——判据自身
  必须对陈旧免疫。reconcile 作为 P1 保留（landed 判据覆盖不到的形态仍需它兜底）。
- **把 `PR_OPEN/READY` 记录整体排除出窗口**：会丢掉「PR 被误关/待 reopen/被取代」的
  风险窗口（v1.9 已就 CLOSED 裁决），且与 §3.2「开放 PR 恒在窗口」矛盾。
- **`landed` 只认 ancestry**：实测 `docs-adr0035-merge` 本地分支 2 ahead/121 behind
  （提交被改写）、`adr-0036-*` 记录 `branch=main`，两者 ancestry 都判不出；加 merge 主题
  通道后再测，残留的「cache 称开放」记录 **7/7 全部在 GitHub 上真开**（零误判）。
  merge 主题只认 `Merge pull request #N from ` 严格形态，不做 `(#N)` 泛匹配。
- **新增 `--decision-topic` / Decision Registry**：违反 §2.3「Registry 不对业务上锁」，
  且样本量仍小；改为 §3.6 的字段封闭性 + §3.5 的既有数据判据。
- **declare 时对共享主检出直接拒绝**：dsh web 等 GUI harness 无法自建 worktree，硬拒会
  阻断真实用法；改为「不归属 + 显式标注」，把选择权留给开发者（ADR §2.1）。

## Verification

- `python tools/dev/ai_work.py --self-test` → 通过；新增四组红绿断言：
  - `zombie_candidate` 9 条（含 `PR_OPEN/READY/CLOSED` 必须为假）；
  - `adr_claims` 3 条（只认具体 ADR 文件，`docs/adr` 目录与 `docs/adr-notes.md` 不误判）；
  - **landed 12 条**，基于临时 git 仓库 fixture（离线、不触网）：ancestry 绿（本地 ref /
    远端 ref 两种形态）、merge 主题绿、未合入/无 PR/主干自身/缓存终态/无分支/仓库不可用
    六种 fail-safe 红；
  - derived 归属 4 条（同分支取档 1；分支不符与共享 worktree 均退回档 2 且**不得**再记
    别人的文件）+ 共享 worktree / ADR 占用纯函数红绿。
- **E2E（真实 registry，同一快照前后对比）**：`status` 在窗 31→11、overlap-hint 151→18、
  zombie-candidate 11→1、`[stale-cache]` 22 条、`[adr-collision]`/`[shared-worktree]`
  正确落点；`drift` advisory 426→67；`status` 耗时 18.1s→5.7s（在窗集合收敛后两两循环
  规模下降）。纯函数复核：`issue_conflicts({1004}/{1113}/…, landed)` 由非空转空，
  「cache 称开放」记录的残留集合与 GitHub `state` 逐个核对**全部为 OPEN**（零误判）。
- **契约预算**：`docs/development/ai/execution-contract.md` 25977/26000 bytes、
  210/260 行；`python tools/dev/check_governance_surface.py --check` → S1–S12 全绿。
- `ruff check tools/dev/ai_work.py` → 通过（首轮 F541 两条已修）。

## Revisit

- **契约已到 99.9% 预算（23 bytes 余量）**：任何后续契约改动必须先做**契约分层**（细则
  分片到第二文档），不得再抬 S6。这是 v1.9 Revisit 的到期条件，本版已逼近。
- **P1（另单）**：① 批量 `reconcile`（单次 `gh pr list --state all` 覆盖全部记录，实测
  1.8s，替代逐条 `update`）；② `status` 的 O(n²) `derived_paths` 重复调用（memoize 原型
  32.3s→1.32s）与视图分层（默认在窗优先、`--all`、`--json`）；③ `drift` overlap 仍为
  顶层目录粒度（本版 67 条 advisory 中仍以它为主），粒度是否收窄到 §5.4 组件级需独立裁决
  （ADR §2.7 P3 明确写的是顶层粒度）。
- **merge 主题通道的脆弱点**：依赖 GitHub merge commit 主题格式；若上游改格式或仓库改用
  squash 合并，通道②静默失效（fail-safe：记录留在窗口 + `stale-cache` 不再触发，退化为
  本版之前的行为）。改用 squash 流程时须复评。
- **记录 `branch=main` 或共用主检出的形态**：本版只做「不归属 + 标注」，若长期存在
  （dsh web 类 GUI harness 无法建 worktree），再议是否要求特定 harness 先建 worktree 或
  提供 worktree 供给能力（属 P2 Adapter 面）。
- **`landed` 后被 revert**：merge 主题仍在 → 记录保持出窗，直到有人重新 `declare`；
  这是「按主干事实判定」的固有边界，出现真实事故时再议。
- 本版为纯本地判据；若 registry 规模继续增长使 `git log --merges` 与逐条 rev-parse 变慢，
  再议缓存 merge 主题集合（当前 123 条记录下不可观测）。
