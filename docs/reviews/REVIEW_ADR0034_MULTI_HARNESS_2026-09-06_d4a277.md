# ADR-0034 多 Harness 执行契约 —— 只读评审与验收（v0.3 → v0.5 → Accepted → 批次首单）

- **状态**：Living（评审三轮已结稿；第四部分为完结状态确认，第五部分为批次首单 dogfood 验收）
- **日期**：2026-09-06（§0–§14）；2026-09-07 增补（§15–§18）
- **性质**：**只读评审 / 只读确认**（非 ADR、非 Agent Note）——全部环节均**未改动被评对象任何一行**
- **被评对象**：[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)——**v0.3**（§0–§6）、**v0.4**（§7–§10）、**v0.5**（§11–§14）、**完结状态**（§15–§16）、**批次首单**（§17–§18）
- **产出会话**：resume `afacd042-6df8-47f3-b632-a14ad6d4a277`（后六位 `d4a277`，文件名尾缀）
- **评审范围**：ADR 全文（§1–§6 + 附录 A）、配套 note [`2026-09-06-adr-0034-draft.md`](../notes/process/2026-09-06-adr-0034-draft.md)、八源综合裁决 [`REVIEW_…_synthesis.md`](./REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md)。**未读**：方案原始全文（折叠部分）与其余 7 源原始报告，故不对 ADR 外细节、及 R 编号映射本身的准确性作独立判断——第二轮仅核验**各 R 项在 v0.4 中的落点**
- **方法**：仓库内直接核验（行数、引用文件存在性、`git rev-parse` 行为实测、关联 issue/PR 状态、文档同步项逐条外部核验）+ 与既有约定（[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)、#847）交叉比对

---

> **阅读指引**
>
> | 部分 | 章节 | 对象 | 结论 |
> |---|---|---|---|
> | 一 | §0–§6 | v0.3 | 修完 2 项必修再 Accepted |
> | 二 | §7–§10 | v0.4 | 建议 Accepted |
> | 三 | §11–§14 | v0.5 | 建议 Accepted（一处文字不一致建议同批修） |
> | 四 | §15–§16 | Accepted v1.2 | 裁决已完结；实施剩 P0b 收尾 |
> | 五 | §17–§18 | 批次首单 #880 | 报告属实；工具链经实战验证 |
> | 六 | §19–§20 | 收尾裁定复核 | **本节三条判定被推翻，见 §19 的勘误表** |
>
> **各轮冲突处以时间在后者为准。**⚠️ **§15–§16 的三条「未完结」判定中，已有两条在 §19 被证伪，阅读时必须与 §19 同读。**

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

> **【2026-09-07 后续】已立单 [#946](https://github.com/DUElost/stability-test-platform/issues/946)**
> （`[devx][contract] ai_work lifecycle 无 FINISHED→CODING 回退`，`tech-debt`）。
> 本条当时是**理论判断**（契约推演），#946 补齐了代码层证据与最小复现：
> `ai_work.py:403` 是全文件唯一写入 `"CODING"` 处（仅 `declare` 新建记录），
> `cmd_finish` 无条件写 FINISHED、`cmd_update` 不写 lifecycle。
> 另补一条当时未识别的后果：换新 id 重新 declare 会导致**两条记录自指 overlap**
> （旧记录 FINISHED+PR_OPEN 仍在窗、scope 重合）。**讨论与方案以 #946 为准。**

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
- 事实核验以 2026-09-06 仓库状态为准；**v0.5 发布后已开第三轮，见 §11–§14**。

---
---

# 第三部分：第三轮评审（v0.5）

## 11. 第三轮结论摘要

| 维度 | 结论 |
|---|---|
| **可否 Accepted** | ✅ **建议 Accepted**；§2.4 与 §2.2 的措辞冲突建议同批修（一行级） |
| 性质 | v0.5 **不是补措辞，是修真 bug**——质量高于 v0.4 |

## 12. 修得好的（5 项，均经本仓实测）

| 改动 | 核验 |
|---|---|
| **§2.3 真值表重写** | 堵住 `ABANDONED × PR_OPEN` 悬空退窗（执行者放弃、PR 还开着仍在等 FIFO）与 `CODING × CLOSED` 同病。**这正闭合了第二轮观察 2**。逐组合验证：`ABANDONED×MERGED`=false、`ABANDONED×PR_OPEN`=true、`CODING×CLOSED`=true、`ABANDONED×NO_PR`=false——全部正确 |
| **§3 symlink 写保护修正** | v0.4 写「薄壳若为 symlink 则**天然防误写**」是**错的**；v0.5 改为「symlink 消除的是双份内容漂移，**不提供写保护**，经 `CLAUDE.md` 路径写入会穿透修改真身」。修正正确 |
| **§2.2 derived 口径含 untracked** | 指出 `git diff --name-only` 不含新文件。**实测证实**：新建文件在该命令下输出为空，须 `git ls-files --others --exclude-standard` |
| **附录 A Antigravity 未验证** | 明写「不因缺席而视为通过」——诚实 |
| **§2.7 P0 必备目录扩展** | 两处关键自查：①`lifecycle` 与 `pipeline_def` 域 lifecycle 的**术语消歧**（S11 有锚定，真会撞）②**P1 启动判据的数据源口径**——registry 是 P1 产物不能自证，须用 git worktree 历史 |

## 13. 第三轮新观察（6 条）

### 13.1 必修：§2.4 未随 §2.2 的并集语义更新（一行级）
§2.2 改为「并集恒成立……不一致时输出 declaration drift 提示（advisory）」；
§2.4 仍写「不一致时**以 diff 为准**」。并集下声明不会被 diff 丢弃，只是被标 drift——两句互斥。§2.4 是 v0.4 遗留。

### 13.2 僵尸候选判据在并集语义下几乎永不触发
§2.3 判据 = 「lifecycle ∈ {CODING, FINISHED} 且 STALE 且 **effective scope 为空**」。
并集下 effective scope = declared ∪ derived，**只要声明非空就非空**——而声明非空是常态。
结果：僵尸清单基本不触发，陈旧声明持续产生 overlap 噪音。建议改判据为「**derived(diff) 为空** 且 STALE」，或加声明龄阈值。

### 13.3 并集把失败模式从假阴性翻成假阳性，缺兜底
并集 = 不漏报，代价是未清理的声明持续进入 effective scope。§2.2 说过期声明由执行侧显式清理（引 6d0f05 O-1 残留面），但依赖自律。
与 §2.9「不为分类摩擦付协同税」的价值取向相比，这里宁可要噪音也不漏——取舍可辩护，但**建议给假阳性兜底**：drift 提示累计 N 次后强制 `update`，或 status 把「仅来自声明、无 diff 支撑」的 scope 单独标为"意图未验证"。

### 13.4 对账丢了「工具媒介」这一点
抬头对账现为两点（①advisory ②前提已变），v0.4 三点中的「工具媒介自动登记 vs 手工仪式」被去掉。
而 #847 的否决理由核心恰是「**持续性仪式** vs 一次性约定」。且 v0.5 保留声明（并集）+ 要求执行侧显式清理，**仪式感实际比 v0.4 更强**（多了清理动作）。建议补回第三点。

### 13.5 §5 P1 验收未点名真值表新增分支
写的是「overlap 集合分支」，未显式列 `ABANDONED × PR_OPEN`、`CODING × CLOSED`——这正是本次修正的核心，建议点名，防自测只覆盖旧集合。

### 13.6 范围外但相关：AGENTS.md 并行约定已被 #853 压缩
实测 `a015727b`（PR #853，Phase -1 基线）重写 AGENTS.md 后，「多 Agent 并行开发」整节被移除，现只剩 `AGENTS.md:35` 一句「查看其他 worktree 的实际 diff」，**未附任何口径**。
v0.5 §2.2 不再引用「AGENTS.md 派生视图」而是自定口径——**这是对的**，否则会指向已消失的命令。
但 P0 之前现行约定事实无可用口径，建议 §2.2 口径（含 untracked）在 P0a 同步回填。

## 14. 第三轮边界声明

- 第三轮同为**只读评审**，未改动 ADR 或任何配套文件；
- 第二轮 4 条观察中，**观察 2 已被真值表闭合**、**观察 3（三维术语）本版未动**；观察 1（恢复编码回退）、观察 4（test_impact 缺省）未处理，属 P0/P1 实施期事项；
  - **【2026-09-07 后续】观察 1 已立单 [#946](https://github.com/DUElost/stability-test-platform/issues/946)**（含代码证据与最小复现）；观察 4 仍无单据。
- 未读方案原始全文与其余 7 源报告，仅核验 ADR 文本与本仓实测；
- 事实核验以 2026-09-06 仓库状态为准。

---
---

# 第四部分：完结状态只读确认（Accepted v1.2）

> 用户问：「当前 ADR-0034 的开发工作是否完结」。以下为 2026-09-07 仓库状态的只读核验结果。

## 15. 结论：本体裁决已完结，配套实施未完结（剩余项明确且量小）

ADR 于 **v1.0（#865，2026-09-06 用户人工终审批准）Accepted**，现为 **v1.2**。版本链：
`v0.1 #858 → v0.2 #859 → v0.3 #860 → #861（索引同步）→ v0.4 #862 → #863（R6/R18 裁决）
→ v0.5 #864 → v1.0 #865（Accepted）→ v1.1 #866（细则迁出）→ v1.2（P1 启动判据修订）`

### 15.1 已完结（有实证）

| 项 | 证据 |
|---|---|
| 方向裁决 | 状态行 `Accepted（v1.2）`；README 索引标 Accepted |
| **P0a** 契约文档 | `execution-contract.md` 存在，§1–§10 完整，Living v1.1 |
| **P0a** 细则迁出 | ADR §2.2–2.9 已收缩为「决策要点 + 指针」 |
| **P1** Registry MVP | `tools/dev/ai_work.py` 752 行，含 `--self-test`（scope/overlap/真值表/liveness/codec/原子写 红绿双向）；`test_impact` 入 schema（11 处命中） |
| **G2** 真身+薄壳 | `backend/agent/CLAUDE.md → AGENTS.md`、`aee/CLAUDE.md → AGENTS.md` 均为 symlink |
| 契约入门禁 | checker 中 2 处命中（S2 `link_files` + S6） |
| AGENTS.md/CLAUDE.md 指向契约 | AGENTS.md:36、:56；65 行（80 行预算内） |
| 09-04 note 交叉链接 | 抬头明写「已由 ADR-0034（Accepted v1.0）取代」并保留过渡条款 |
| #854 | CLOSED |

## 16. 未完结项

### 16.1 P0b 收尾（2 处，均一行级）
- `.cursor/rules` 未接线——grep 无 `execution-contract` 命中；
- `.codex` 未接线——同上；
- 附带：`2026-09-04-multi-agent-parallel-convention.md` 的 `Status:` 仍为 `implemented`，
  虽正文已写取代关系，但字段未改 `superseded`（§5 P0 验收要求，半完成）。

### 16.2 按设计未启动（非欠账）
P2 Harness Adapter、P3 Drift/Freshness gate 无产物，属分期计划内；P4 Integration Planner 为观察项；
Antigravity 验证附录 A 明标「未验证（延期）」。

### 16.3 关联 issue 仍 OPEN（属预期，非阻塞）
- **#855**：三段触发是「P0 完成 = 补全工作**可开工**」，不等于已完成；
- **#857**：Claude `@import` 子目录不解析，上游缺陷，ADR §6 列为 Revisit 触发项。

### 16.4 值得注意的变化：P1 启动判据被修订
v1.2 增补第一触发「**已计划的多 Harness 批次启动前预置就绪**」（2026-09-07 用户裁决）——
原「等 ≥2 次撞车返工」被判为**因果倒置**。故 `ai_work.py` 的落地是**主动预置**，非判据自然触发。
契约 §9 过渡条款：`ai_work.py` 被采用前仍维持 2026-09-04 派生视图用法，防空窗。

---
---

# 第五部分：批次首单 dogfood 验收（#880 / PR #899）

> 用户报告：多 Harness 批次第一单完成，本会话（Claude Code）作为第一 Execution 全程用 Registry 工具链 dogfood。
> 以下为只读核验结果——**报告全部属实**。

## 17. 外部事实与代码层核验

| 声明 | 核验方式 | 结果 |
|---|---|---|
| PR #899 合入 `3e072f62` | `gh pr view 899` | ✅ MERGED，mergeCommit `3e072f62` |
| #880 自动 CLOSED | `gh issue view 880` | ✅ CLOSED |
| #891 R01 台账 10 项 | `gh issue view 891` | ✅ OPEN「📌 [总表] R01 总体架构与硬契约审查台账（2026-09-07：**10 项**）」 |
| main tip = `3e072f62` | `git log origin/main` | ✅ 首条即 #899 merge |
| 主工作树干净 | `git status --short` | ✅ 空 |
| 无在途 PR | `gh pr list --state open` | ✅ 空 |

**修复三层均已落地**（`tools/dev/ai_work.py`）：

```python
def _quote(v) -> str:
    s = str(v)
    if s == "":
        return '""'
    if not s.startswith("#") and re.fullmatch(r"[A-Za-z0-9_./+=:@-]+", s):
        return s  # 含 # 一律引号——行首裸 # 会被当注释（#880）
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
```

`_quote` 字符集确无 `#` ✅；`normalize_requirement_id` 6 处命中（语义层守门）；`corrupt` 8 处命中（损坏隔离）✅。

## 18. Dogfood 实证（本部分价值最高的一条）

`registry.yaml` 真实落点确为
`$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`——
**契约 §2.2 定的 per-clone root 在实战中解析正确**（986 字节 + `registry.lock`）。

两条记录均走到终态：

```
fix-825-gates-parity      lifecycle: FINISHED   integration_cache: MERGED
fix-880-registry-codec    lifecycle: FINISHED   integration_cache: MERGED
```

按 §2.3 真值表 `MERGED` 出窗——两条均已正确退出风险窗口 ✅。
**这是真值表第一次在真实数据上被验证，不是自测样例。**

### 18.1 观察：`role` 字段两条均为空

符合 §2.9「P1 允许缺省」的设计。但**若 P2 的 Role Context 定义归属尚无落点，该字段会持续为空**，
而 §2.7 P2 的验收项是「会话启动时知晓自身 Role」。dogfood 已实测证明 **Role 字段当前实际消费为零**——
这比事后推测更准，建议作为 P2 启动时的输入。

### 18.2 留痕修正：「第一单」表述

registry 中 `fix-825-gates-parity` 早于 `fix-880-registry-codec`。
若 #825 同属本批次，则「第一单」宜表述为「**本会话承担的第一单**」——
不影响结论，但审计留痕上值得准确。

### 18.3 本部分边界声明

- 全部为只读核验，未改动任何文件；
- 未重跑 `ai_work.py --self-test`，对其自测结论不作独立复验；
- 未复核 #899 的 CodeQL ReDoS 修复细节（仅确认提交 `6ce81f80` 存在于 main）；
- 事实核验以 2026-09-07 仓库状态为准。

---
---

# 第六部分：收尾裁定复核（**推翻第五部分三条判定中的两条**）

> 本节由用户报告触发的复核。**§16.1 与 §18.2 的部分判定有误，以下为勘误。**
> 保留原文不删——审计需要看到错误是如何被推翻的，删掉会让后续读者无从判断。

## 19. 勘误表（本节最重要）

| # | 原判定（§15–§18） | 复核结论 | 性质 |
|---|---|---|---|
| 1 | 「#843/#847/#848 构成 Registry 可见性缺口」 | **错**。三者是 09-04/09-05 产出，早于 Registry 工具本身（#878，09-07），时间上不可能 declare；回溯登记终态无意义 | 事实错误 |
| 2 | 「近期评审类会话未 declare = 缺口」 | **部分成立但定性错**。是**采用引导问题**，非契约或工具缺陷——契约本就是 visibility-only 自声明（从不上锁、无强制），`harness-adapters.md` P2 动作表原有「whoami 无记录 → 提示 declare」指引，该会话未执行 | 定性错误 |
| 3 | 「零 diff 印证 §2.2 声明不可替代」 | **举据不严谨**。该会话实际开了 PR（#911/#913 改 docs/reviews），diff 在分支与 GitHub 可见。「两头不见」只在两个窗口成立：文件未写出之前（意图先于 diff——§2.2 的真命题）与 PR 合入且 worktree 删除之后 | 举据错误 |
| 4 | 「`.codex` 未接线」 | **错**。`.codex/hooks.json` 是纯 hooks 配置、无文档通道（实测内容为 Stop → tsc 命令）；Codex 的规则入口是根 AGENTS.md，P0b 已在根文件接了 contract 指针 | 事实错误 |
| 5 | 「09-04 note `Status:` 未改 superseded = 半完成」 | **错**。S10 校验的 Status 枚举为 `proposed/implemented/rejected`（`check_governance_surface.py:254`），**无 `superseded` 值**，改了会红；「头部取代注记 + 原文留档」是 P0b Note Alternatives 里明确记录的裁决，有 RESIDENT_CONTEXT_AUDIT 先例 | 事实错误 + 误读设计 |
| 6 | 「`.cursor/rules` 未接线」 | **成立**。ADR §2.7 P0b 薄入口清单明列 `.cursor/rules`，属自身范围遗漏 | ✅ 判定正确 |

### 19.1 已完成的收尾（PR 均经核验合入）

| PR | commit | 内容 |
|---|---|---|
| #918 | `1c83c270` | P0b 补遗——`.cursor/rules` 接线 execution-contract（`00-project-context.mdc:13`，常驻路由、S6 预算内）；顺带修 `agent-runtime.mdc` 的 G2 遗留（scoped `CLAUDE.md` → `AGENTS.md` 真身引用） |
| #919 | `8aa799e7` | P2 动作表补一行「文档/评审类会话（改 docs/reviews、issue 评论、PR 评审）同样 declare（scope=意图目录）」 |

`.codex` 按「无文档通道」口径**关闭核查项**，非遗漏。
`harness-adapters.md:87` 采用的行文为准确表述：「diff 未产生时派生视图无信号，GitHub 只见产出不见意图」——
**原「两头不可见」的结论方向对，但例据（该会话零 diff）不成立。**

### 19.2 复核后的净结论

ADR-0034 的 **P0 遗漏清零、P2 指引补强完成**。第五部分 §16.1 列出的三处收尾中：
`.cursor/rules` 属实并已修；`.codex` 与 note Status 两条**本就不是收尾项**，是我的误读。

## 20. 本部分边界声明与自我观察

### 20.1 边界声明

- 本部分为**只读复核**，未改动 ADR、契约或任何配置；
- 核验项：#918/#919 合入与 commit、`.cursor/rules/00-project-context.mdc:13`、
  `.codex/hooks.json` 实际内容、`check_governance_surface.py:254` 枚举、note L6–L9 头部注记、
  `harness-adapters.md:87` 新行、main tip `8aa799e7`、工作区干净；
- 未复核 #918 中 `agent-runtime.mdc` G2 遗留修复的具体内容（仅确认 PR 标题含此描述）；
- 事实核验以 2026-09-07 仓库状态为准。

### 20.2 自我观察：三处错误的共同根因

第 1、3、4、5 条是**同一类错误**——**以字符串命中与否代替机制判断**：

- `.codex`：grep 无命中即判「未接线」，未追问「该文件的用途是否本就有此通道」；
- note Status：见 `implemented` 与取代注记并存即判「字段遗漏」，未查枚举约束；
- 「零 diff」：断言无 diff，未查是否已有 PR 承载。

同会话早前还有一次同类：`gh pr view --json merged` 的字段名报错被误判为网络故障。

**教训（已写入项目记忆）**：下「缺失 / 未做 / 有洞」类结论前，必须先答三个问题——
① 该字段的合法取值是什么（查校验器枚举，不假设）；② 该文件的设计用途是否包含所期待的通道；
③ 该改动是否已以别的形式存在（PR / 分支 / 另一文件的注记）。答不了就写「未确认」，不写「缺失」。

### 20.3 版本快照提示

本文件 §15 记录 ADR 为 **v1.2**；复核时仓库实况已是 **v1.3**（#911 标题提及 v1.4）。
ADR 版本推进快于本文件的观察节奏——**引用版本时以仓库实况为准，勿沿用本文件快照。**

### 20.4 本文件遗留观察项的最终去向

| 观察 | 提出处 | 去向 |
|---|---|---|
| lifecycle 缺 `FINISHED→CODING` 回退 | §9.1 | ✅ 已立单 **[#946](https://github.com/DUElost/stability-test-platform/issues/946)**（含代码证据 + 最小复现 + 三方案） |
| 目录级 scope 的 drift 恒定假阳性 | §13.3 | ✅ 已独立立单 **#928**（本文件未提，由批次另行识别；`/tmp/stp-928` 在修） |
| `test_impact` 缺省=indirect 致 coverage-mismatch 召回率依赖自愿声明 | §9.4 | ⬜ **仍无单据**——若该 advisory 长期空转需重议，建议届时立单 |
| 僵尸候选判据在并集语义下几乎不触发 | §13.2 | ⬜ 仍无单据，与 #928 同属「并集假阳性」家族，可考虑并入 |

**注**：#928 与 #946 均非本文件直接产出单据，但问题与本文件观察同源；
此处回链是为让后续读者能沿线索走通，不主张归属。
