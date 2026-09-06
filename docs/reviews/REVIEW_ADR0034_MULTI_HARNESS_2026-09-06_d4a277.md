# ADR-0034 多 Harness 执行契约 —— 只读评审（v0.3 草案）

- **状态**：Final（2026-09-06 一次性只读评审，结论不随 ADR 演进修订；ADR 修改后需另开评审）
- **日期**：2026-09-06
- **性质**：**只读评审**（非 ADR、非 Agent Note）——未改动被评对象任何一行
- **被评对象**：[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)（Proposed v0.3，待人工评审）
- **产出会话**：resume `afacd042-6df8-47f3-b632-a14ad6d4a277`（后六位 `d4a277`，文件名尾缀）
- **评审范围**：ADR 全文（§1–§6 + 附录 A）与配套 note [`2026-09-06-adr-0034-draft.md`](../notes/process/2026-09-06-adr-0034-draft.md)。**未读**：方案原始全文（折叠部分），故不对未出现在 ADR 内的细节作判断
- **方法**：仓库内直接核验（行数、引用文件存在性、`git rev-parse` 行为实测、关联 issue 状态）+ 与既有约定（[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)、#847）交叉比对

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

## 6. 评审边界声明

- 本次为**只读评审**，未改动 ADR-0034 或其配套 note 任何一行；
- 未读被折叠的方案原文，对未出现在 ADR 内的细节（registry schema 字段级定义、
  Integration Planner 输入输出、Role 的完整取值域）不作判断；
- 核验以 2026-09-06 仓库状态为准；ADR 后续演进需另开评审。
