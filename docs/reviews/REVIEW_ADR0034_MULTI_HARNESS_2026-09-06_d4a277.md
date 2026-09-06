# ADR-0034 多 Harness 执行契约 —— 只读评审（v0.3 → v0.4 两轮）

- **状态**：Living（第一轮 v0.3 已结稿；第二轮 v0.4 结论见 §7–§10）
- **日期**：2026-09-06
- **性质**：**只读评审**（非 ADR、非 Agent Note）——两轮均**未改动被评对象任何一行**
- **被评对象**：[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)——**第一轮 v0.3**（§0–§6）、**第二轮 v0.4**（§7–§10）
- **产出会话**：resume `afacd042-6df8-47f3-b632-a14ad6d4a277`（后六位 `d4a277`，文件名尾缀）
- **评审范围**：ADR 全文（§1–§6 + 附录 A）、配套 note [`2026-09-06-adr-0034-draft.md`](../notes/process/2026-09-06-adr-0034-draft.md)、八源综合裁决 [`REVIEW_…_synthesis.md`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)。**未读**：方案原始全文（折叠部分）与其余 7 源原始报告，故不对 ADR 外细节、及 R 编号映射本身的准确性作独立判断——第二轮仅核验**各 R 项在 v0.4 中的落点**
- **方法**：仓库内直接核验（行数、引用文件存在性、`git rev-parse` 行为实测、关联 issue/PR 状态、文档同步项逐条外部核验）+ 与既有约定（[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)、#847）交叉比对

---

> **阅读指引**：§0–§6 为 **v0.3 第一轮**（结论：修完 2 项必修再 Accepted）；
> §7–§10 为 **v0.4 第二轮**（结论：建议 Accepted）。**两轮冲突处以第二轮为准。**

---

## 0. 结论摘要（TL;DR）

| 维度 | 结论 |
|---|---|
| **方向** | ✅ 认可。核心模型（Requirement → Harness → Execution）、三条非目标（不通信 / 不共享上下文 / 不实时协调）、Repository-Mediated Integration 均成立 |
| **可否 Accepted** | ⚠️ **建议修完 2 项必修再 Accepted** |
| **必修（P0）** | ① overlap 检测未绑定 git diff；② registry root 用相对路径有实现陷阱 |
| **建议修（P1）** | 与 #847 裁定缺显式对账；Alternatives 对派生视图的否定理由不准确；G2 symlink 缺写入方向约定 |
| **不建议动** | §2.3 两维正交、事实来源分层、§2.5 TTL 分期、§2.8 边界、P3 不建 merge queue、§2.10 权威源分家、附录 A 探针协议 |

**一句话**：v0.3 的加固质量很高，但「diff 优先」这条原则**只写在 §2.4，没有接进 §2.2 的 overlap 检测**——原则对了，线没连上。

---

## 1. 已核验属实

| ADR 声明 | 核验方式 | 结果 |
|---|---|---|
| AGENTS.md 现 63 行（§5 P0） | `wc -l < AGENTS.md` | ✅ 63 |
| 4 处引用文件存在 | `test -f` | ✅ `harness-adapters.md`、两份 09-05 基线 note、09-04 note 均在 |
| #854 / #855 / #857 存在且性质吻合 | `gh issue view` | ✅ 三单均 OPEN，标题与 ADR 引用一致 |
| 多 Harness 常态化前提（§1） | 附录 A 实测绘验 | ✅ 协议含唯一探针串 + 双题设计，可复现 |

---

## 2. 必修（P0）

### 2.1 overlap 检测没有绑定 git diff

**位置**：§2.4（L66-68）声明「diff 优先」；§2.2（L41-44）/ §2.3（L46-64）/ §2.8（L89-93）的 overlap 建立在**声明 scope** 上。

**问题**：全文没有任何一句要求 overlap 检测去读 git。原则写在 §2.4，实现契约在 §2.2，两者断开。

**这不是理论担忧——有实测反例。** 2026-09-04 唯一一次真实碰撞：

```
docs/drift-sync-2026-09-04
   声明命名空间（分支名） = docs
   实际 diff              = AGENTS.md, backend, CLAUDE.md, docs, .github
```

声明会写 `docs/`，实际动了 `backend/` 与 `.github/`。**只按声明做 overlap，恰好漏掉真实发生过的那一次。**

**建议改法**（可直接并入 §2.2 / §2.3）：

> overlap **参与集合**按 integration ∈ {NO_PR, PR_OPEN, READY}（§2.3 的裁定不变）；
> 但**参与者的 scope 数据取 `declared ∪ derived(diff)`，两者冲突时以 derived 为准**。
> 声明仅在「该 Execution 尚无任何 diff」时作为唯一信号——那才是它不可替代的位置。
> `derived(diff)` 口径沿用 AGENTS.md 派生视图：对 merge-base 取差异，含未提交改动。

**配套**：§5 的 P1 红绿样例需显式包含「声明 scope ≠ 实际 diff」fixture（即上例），否则此缺口不会被自测发现。

### 2.2 registry root 用相对路径有实现陷阱

**位置**：§2.2（L42）`$(git rev-parse --git-common-dir)/ai-work/`。

**实测**（git 2.47.3，本仓库）：

| 调用位置 | 输出 | 性质 |
|---|---|---|
| 仓库根 | `.git` | **cwd 相对** |
| `backend/agent/` | `../../.git` | **cwd 相对** |
| linked worktree | 绝对路径 | 与上述**不一致** |

主 worktree 内返回相对路径，裸拼接在 `git -C <path> rev-parse` 调用下会算错（git 相对 `-C` 目录输出，调用方 cwd 未变）。多 worktree 场景返回绝对——行为不一致，实现必踩。

**已验证的修法**：

```bash
git rev-parse --path-format=absolute --git-common-dir
# → /home/debian13/stability-test-platform/.git
```

建议在 §2.2 直接写死 `--path-format=absolute`，不要留给实现自由解释。

---

## 3. 建议修（P1）

### 3.1 与 #847 的裁定缺一次显式对账

ADR 头（L11）声明取代 09-04 note，并点名其含「#847 不为 N=2 引入 WIP 公告类机制」裁定。而 Registry 本质就是一个**声明机制**。

ADR 需明说**为什么这次不同**，例如：

- 工具媒介（CLI 自动执行）vs 手工仪式（每次开工读写文档）
- advisory / visibility-only vs 绑定约束
- N 已跨 Harness 增长，不再是 N=2 单机单 Harness 形态

§2.8（L93）保住了「Role 不是 ownership 边界」，说明连续性在；但 WIP 公告那条否决理由没人对账。否则未来读者会以为是静默反转。

### 3.2 Alternatives 第一条理由不准确

**原文**（§4，L128）：「派生视图只能看本机 diff，跨 worktree 的声明面无载体」。

**事实**：AGENTS.md 现役派生视图遍历 `git worktree list`，**已覆盖本机全部 worktree，因此天然跨 Harness**（各 Harness 均在本机 worktree 内工作）。

真实缺口比文中所述更窄：**只有「两个 Execution 都还没产出任何 diff」时，派生视图才无解**。

建议按这个更窄口径重写理由。否则收益被高估，而 §2.2 的成本（flock / fsync / 原子 rename / TTL / overlap 检测）论证就站不住。

### 3.3 G2 symlink 缺写入方向约定

§3（L117）定「scoped AGENTS.md 真身（中立内容）+ CLAUDE.md 薄壳」，但未说明 symlink 指向哪一侧。

若 `CLAUDE.md` 是 symlink 指向 `AGENTS.md`，任何工具**写** `CLAUDE.md` 都会穿透改写真身（或替换链接本身）。建议补一句方向约定 + 写入防护。

---

## 4. 强项（不建议动）

| 项 | 位置 | 评价 |
|---|---|---|
| liveness × integration 两维正交 | §2.3 L46-64 | 「finish 后 STALE 的在途 PR 仍在集成窗口」反例是对的，且不显然 |
| 事实来源分层表 | §2.3 L55-63 | Registry / Git / GitHub / CI 各管一段，MERGED 只由 GitHub 确认——权威边界清晰 |
| TTL 分期 | §2.5 L70-73 | P1 无心跳源就只 advisory，P2 有 heartbeat 才升格；拒绝硬 TTL 误判长任务——正确 |
| Scope MVP 边界 | §2.8 L89-93 | 明确不支持 glob / ownership / 自动拆分 / semantic / locking；Role 非 ownership 边界——保住了 09-04 的自由度原则 |
| P3 不建 merge queue | §2.7 L86 | 与既有 FIFO auto-merge + update-branch + strict 分支保护一致，不重复造 |
| ADR / Contract 分家 | §2.10 L101-111 | `execution-contract.md` 为唯一权威源，AGENTS.md 保持 minimal bootstrap 不空壳化——干净 |
| 附录 A 探针协议 | L152-163 | 唯一探针串 + 双题设计（阳性对照 + 目标串），结论可复现 |

---

## 5. 附：必修项可直接采用的改法草案

### 5.1 补进 §2.2 末（overlap 数据源）

```markdown
- **overlap 数据源 = declared ∪ derived(diff)，以 derived 为准**：
  Registry 只提供意图信号；每个 Execution 的 `derived(diff)`
  （对 merge-base 取差异，含未提交）由 `ai_work` 在执行时现算并与声明求并集。
  声明仅在 `derived(diff)` 为空时单独生效。
  Registry 从不凭声明单独判定 overlap（反例：2026-09-04 `docs/drift-sync-*`
  声明命名空间为 `docs`，实际 diff 触及 `backend/` 与 `.github/`）。
```

### 5.2 替换 §2.2 registry root 一行

```markdown
- **Registry root = `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`**
  ——必须用 `--path-format=absolute`：主 worktree 内 `--git-common-dir` 返回
  cwd 相对路径（仓库根 `.git` / 子目录 `../../.git`），linked worktree 返回绝对路径，
  裸拼接在 `git -C` 调用下会算错。所有 worktree 经 common dir 解析到同一位置。
```

---

## 6. 第一轮评审边界声明

- 第一轮为**只读评审**，未改动 ADR-0034 或其配套 note 任何一行；
- 未读被折叠的方案原文，对未出现在 ADR 内的细节（registry schema 字段级定义、
  Integration Planner 输入输出、Role 的完整取值域）不作判断；
- 核验以 2026-09-06 仓库状态为准；**v0.4 发布后已开第二轮，见 §7–§10**。

---
---

# 第二部分：第二轮评审（v0.4）

## 7. 第二轮结论摘要

| 维度 | 结论 |
|---|---|
| **可否 Accepted** | ✅ **建议 Accepted**（无阻断项） |
| 第一轮 5 条意见 | ✅ 全部闭合（对应 R1 / R5 / R19 / R21） |
| synthesis 22 项 R 裁决 | ✅ 全部在 v0.4 落地（§8.1） |
| R14 文档同步族（ADR 外） | ✅ 四项外部核验通过（§8.3） |
| 新观察 | 4 条，**均不阻断**（§9） |

**一句话**：v0.4 不是小修——22 项 R 全部落地，第一轮提的 5 条也全闭合，且多数落得比建议更完整
（例：R5 除接线 overlap 与 diff 外，还在 §5 P1 验收加了「声明 scope ≠ 实际 diff」fixture）。

---

## 8. 第一轮意见闭合核验

### 8.1 ADR 内落点（第一轮 5 条 → R 编号 → v0.4）

| 上轮意见 | R | v0.4 落点 | 核验 |
|---|---|---|---|
| 必修1：overlap 检测未绑定 git diff | R5 | §2.2：`overlap = declared ∪ derived(diff)`，冲突以 derived 为准；声明仅在零 diff 时单独生效；附 2026-09-04 实测反例 | ✅ 且 §5 P1 增「声明≠diff」fixture（超出建议） |
| 必修2：registry root 用相对路径 | R1 | §2.2 固定 `--path-format=absolute --git-common-dir`（git ≥ 2.31）为**唯一发现方式**；删除 common dir 外替代落点 | ✅ §5 要求主 checkout 根/子目录/linked worktree 三处解析一致 |
| 建议1：与 #847 缺显式对账 | R21 | 抬头「取代对象」处三点对账：①工具媒介自动登记 vs 手工仪式 ②advisory/visibility-only ③N 已跨 Harness | ✅ |
| 建议2：Alternatives 首条理由不准确 | R21 | §4 首条改窄口径：明写派生视图实已覆盖本机全部 worktree，真实缺口 = 双方均零 diff 时 + 偏离无留痕 | ✅ |
| 建议3：G2 symlink 缺写入方向 | R19 | §3 方向固定 `CLAUDE.md → AGENTS.md`；写入防护（编辑一律落真身，退化时只允许 `@AGENTS.md` 单行）；验收加「根 bootstrap 与 scoped 同时可见」 | ✅ |

### 8.2 新增引用与事实声明

| v0.4 声明 | 核验方式 | 结果 |
|---|---|---|
| synthesis 文件存在（抬头 L11） | `test -f` | ✅ `REVIEW_…_synthesis.md` 存在 |
| `execution-contract.md` 尚不存在 | `test -f` | ✅ 如期为 P0 产物，非缺陷 |
| S11 由 #856 引入（§1 L21） | `gh pr view 856` | ✅ MERGED「S11 硬不变量锚点检查 + 移除 L1 行为 eval」 |
| G2 目标文件存在（§3） | `test -f` | ✅ `backend/agent/CLAUDE.md`、`backend/agent/aee/CLAUDE.md` 均在 |
| 薄入口列举（§2.7/§2.10） | `test -d` | ✅ `.cursor/rules/`、`.codex/` 均在 |
| S2 `link_files` / S6 `RESIDENT_BUDGETS` 锚点存在 | `grep` checker | ✅ 命中 |
| AGENTS.md 仍 63 行（§5 的 80 行预算前提） | `wc -l` | ✅ 63 |

### 8.3 R14 文档同步族（ADR 之外，唯一须外部核验的一项）

| 项 | 结果 |
|---|---|
| `docs/adr/README.md` 主表补 0034 行 | ✅ 已补 |
| `docs/DOC-MAP.md` 引用 0034 | ✅ 已补 |
| 治理面设计文档 :29 → S1–S11 | ✅ 实测 `docs/design/2026-08-governance-surface-protection.md:29` 已为「确定性文本检查 S1–S11」 |
| 起草 note 开篇锚 v0.4 | ✅ |
| P0「现 63 行」快照移除 | ✅ v0.4 §5 已无该快照 |

---

## 9. 新观察（4 条，均不阻断 Accepted）

### 9.1 lifecycle 缺「恢复编码」回退路径

§2.3 定义 `lifecycle ∈ {CODING, FINISHED, ABANDONED}`，`finish` → `FINISHED`。
但「PR 评审意见回来 → Harness 恢复编码」这条现实路径**没有定义 `FINISHED → CODING`**。
结果是审计记录写「已停止编码」而实际在编码——R6 引入 lifecycle 正是为了表达停止/继续，
回退路径缺失使其不完整。

**建议**（P0 transition table 二选一并写死）：允许 `FINISHED → CODING`；
或明确「恢复编码 = 新开 Execution」。

### 9.2 `finish --abandon` 与 integration 终态冲突未定义

`lifecycle=ABANDONED` + `integration=MERGED` 语义矛盾（已合入的工作被标放弃）。
R2 已把 transition table 列为 P0 必备目录，建议在其中显式写明：
`integration ∈ {MERGED, CLOSED}` 时 `finish --abandon` 应拒绝或 no-op，避免污染审计记录。

### 9.3 「三维」表述与 liveness 不持久化存在张力（术语精度）

§2.3 标题为「lifecycle × liveness × integration **三维正交**」，但同节明写
liveness「查询时派生、不持久化」。把派生量与两个持久字段并列称「维」，
实现者易误以为 liveness 也需落库。

实际上**持久维度是两条**（lifecycle × integration），liveness 是查询时派生——
这与冻结版 Contract v1 的「两维」应当是一致的，只是措辞造成歧义。
建议改称「两维持久 + 派生 liveness」，或在 §2.3 首句加一句显式区分。

### 9.4 `test_impact` 缺省=indirect 使 coverage-mismatch 召回率取决于自愿声明率

§2.9 的 mismatch 判定只针对 `none`（声明不涉行为但 diff 触及测试路径）与
`direct`（声明直接但无测试运行记录）两种；而同节允许**缺省（缺省=indirect）**。
因此**默认路径不参与检测**。

这是 R12 的有意取舍（「不为分类摩擦付协同税」），取舍本身认同。
但建议在 contract 中记录该取舍与重议条件：若显式声明率长期偏低，该检测实际空转，
届时要么改为强制，要么撤掉——避免长期挂一个不产生信号的 advisory。

---

## 10. 第二轮边界声明

- 第二轮同为**只读评审**，未改动 ADR-0034、synthesis 或任何配套文件；
- 8 源审查中仅完整读了本文件（`d4a277`）、synthesis 总表与 ADR v0.4；
  **其余 7 源原始报告未逐篇读**，故不对 R 编号映射本身的准确性作独立判断，
  仅核验各 R 项在 v0.4 中的落点是否到位；
- 未读方案原始全文（折叠部分），对 ADR 外的 registry schema 字段级定义、
  Integration Planner 输入输出、Role 完整取值域不作判断；
- 事实核验以 2026-09-06 仓库状态为准；ADR 若再有 v0.5+ 演进需另开评审。
