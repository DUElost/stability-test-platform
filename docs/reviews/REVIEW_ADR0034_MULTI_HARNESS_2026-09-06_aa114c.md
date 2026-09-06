# ADR-0034 多 Harness 执行契约只读审查报告（Proposed v0.4）

- 审核日期：2026-09-06
- 审查类型：系统性只读审查与架构审计（未修改任何生产代码）
- 审查对象：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.4，commit `25e9e6c7` / PR #862 & #863）
  - `docs/reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`（八源审查综合裁决，R1–R22 权威映射）
  - `docs/notes/process/2026-09-06-adr-0034-draft.md`（起草与修订历程）
  - `docs/adr/README.md`（ADR 索引，主表已收录）
  - `docs/DOC-MAP.md`（文档地图，已建立索引）
- 会话 Resume 标识：`aa114c`（Conversation ID: `2357bf61-1c1b-4139-b5be-378318aa114c`）

---

## 一、 审查结论与总体定性

**ADR-0034 v0.4 无阻断性缺陷（No Blockers），无 A 级缺陷。契约完备度极高，边界定义已无任何模糊死角，正式建议进入 Accepted 状态。**

从 v0.3 到 v0.4，文档系统性地吸收了同日落库的 8 份独立只读审查成果，经过综合裁决（`synthesis.md`，R1–R22 权威编号映射），一次性落地了 **21 项精确采纳** 与 **2 项关键人工裁决**（R6 状态模型实现属性澄清、R18 竞争模式裁决不入）。它终结了“状态机如何闭环”、“多 Worktree 解析一致性”、“真实 Diff 偏离声明”、“过早过度设计”等全部争议点，完全具备作为跨 Harness 唯一执行基准的严肃性与可行性。

---

## 二、 门禁与机械校验记录

在最新代码基座（commit `1715eee6`）上执行了完整的质量与治理结构门禁：

| 门禁 / 校验项 | 实际运行命令 | 校验结果 | 备注 |
|---|---|---|---|
| **快速静态门禁（6 gates）** | `venv/bin/python scripts/run_gates.py check:quick` | **PASS (0)** | ruff、eslint、tsc、knip、compileall、gov-surface 全绿（耗时 ~19s） |
| **治理面结构门禁（L0）** | `python3 tools/dev/check_governance_surface.py --check` | **PASS (0)** | S1–S11、S5x 阻塞项全部通过 |
| **治理面红绿双向自测** | `python3 tools/dev/check_governance_surface.py --self-test` | **PASS (0)** | 12 条规则的正反样例双向验证全部通过 |
| **常驻入口预算控制（S6）** | `check_governance_surface.py:check_resident_budgets` | **PASS** | `AGENTS.md` 64 行 / 3815B（≤80行/8KB）；`CLAUDE.md` 27 行 / 978B（≤60行/6KB） |
| **硬不变量在场防护（S11）** | `check_governance_surface.py:check_hard_invariant_anchors` | **PASS** | 11 条原文正则锚点对当前 `AGENTS.md` 100% 命中 |
| **README 主表收录（R14）** | 检查 `docs/adr/README.md:85` | **PASS** | ADR-0034 已正式收录进 `## 当前 ADR 清单` 主表（行 85） |
| **DOC-MAP 架构索引（R14）** | 检查 `docs/DOC-MAP.md:83` | **PASS** | 架构 ADR 列表已正式链接 ADR-0034 及 `synthesis.md` |

---

## 三、 v0.4 关键加固与 R1–R22 裁决逐项核验

### 1. 状态模型三维正交与语义闭环（R2, R3, R4, R6）
* **R6 裁决背景明确**：明确指出用户确认的 Contract v1 核心约束为“两维”（Execution × Integration），而 v0.4 采用的 `lifecycle × liveness × integration` 三维结构是**“解决 finish 缺少独立可持久化语义表达、兼载中途放弃的合规实现选择，而非冻结条款”**。
* **各维度权威职责严格分离**：
  * `lifecycle ∈ {CODING, FINISHED, ABANDONED}`（执行侧自声明）：`finish` 仅写本字段（解耦提 PR 的先后顺序）；`ABANDONED` 严格限定为**显式人工动作（`finish --abandon`）**，永不因超时或命令自动产生。
  * `liveness ∈ {LIVE, STALE}`（**永远纯为 Advisory**）：持久层只存 `last_seen`；STALE 为查询时动态派生值，持久层不存、不回写，绝不改变业务语义，绝不将记录移出集成窗口。
  * `integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}`（GitHub 权威）：补齐 `CLOSED`（PR 关闭未合入）终态；`READY`（checks 全绿）由 `update` 检查 GitHub 派生刷新，主干推进导致重跑时自动回退为 `PR_OPEN`。
* **R4 观察者语义守卫**：明确 `status` 命令**严格只读**，绝不改变被观察对象的 `last_seen`。仅携带 execution identity 的写命令（`declare/update/finish`）可刷新自身的 `last_seen`。
* **Overlap 集合闭环**：`lifecycle ∉ {ABANDONED} 且 integration ∈ {NO_PR, PR_OPEN, READY}`，Liveness 完全不参与过滤。僵尸记录通过 `status` 输出的孤儿清单人工经 `finish --abandon` 终结。

### 2. 存储拓扑与原子写九步全序（R1, R7）
* **R1 唯一解析发现方式**：固定使用 `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`。
  * 彻底消除了裸 `--git-common-dir` 在主 checkout 返回相对路径（`.git` 或 `../../.git`）而在 linked worktree 返回绝对路径的平台与目录层级陷阱；
  * 明确删除了在 common dir 之外指定路径的含糊表述，确立 **Registry 按 Clone 物理隔离**，与派生视图同口径。
* **R7 原子写九步全序**：
  `flock(registry.lock) → read registry.yaml → validate → modify → write same-dir tmp → fsync(tmp) → rename → fsync(parent dir) → unlock`
  补齐了 `parent directory fsync`，彻底封死系统崩溃时目录项未刷盘导致的文件系统丢失漏洞。

### 3. Overlap 事实优先与真实反例对账（R5）
* **数据源合成公式**：`overlap 数据源 = declared ∪ derived(diff)`，当声明与实际产生冲突时**以 diff 产生的派生范围为准**。
* **实证支撑**：声明仅在 diff 为空时单独生效（编码前的先验意图）；一旦产生代码，diff 作为唯一真理介入。文内正式引用了 2026-09-04 `docs/drift-sync-*` 声明为 `docs` 实际触及 `backend/` 与 `.github/` 的真实事故作为依据，说服力极其扎实。

### 4. 治理架构与分期边界防护（R8, R9, R10, R11, R22）
* **单一规范权威源（R8/R9）**：P0 建立 [`docs/development/ai/execution-contract.md`](file:///home/debian13/stability-test-platform/docs/development/ai/execution-contract.md) 时，将 ADR 中的细节**一次性平移**入内，ADR 正文收缩为决策要点与历史记录，坚决避免 ADR 与 Contract 文档形成双份事实源；
* **薄入口接线与门禁（R10/R11）**：P0 补全 [`docs/development/repository-workflow.md`](file:///home/debian13/stability-test-platform/docs/development/repository-workflow.md) 与基线 Note 指针接线，并将 `execution-contract.md` 纳入 S2 相对断链检测与 S6 体量预算管理；
* **P1 启动判据（R22）**：在 §2.7 P1 增设了理性门槛——**“连续两周并行 worktree ≥3，或实际发生 ≥2 次跨 Harness 撞车返工”**。在触发前继续维持 2026-09-04 派生视图，坚决不为无业务痛点的场景过早搭建复杂工具链。

### 5. 质量纪律与周边规则闭环（R12, R13, R17, R18, R19, R20, R21）
* **Scope 拒绝规则（R17）**：明确拦截绝对路径、`..` 越级、符号链接穿透与 trailing slash 歧义，引入路径组件边界判定；
* **测试与注意力预算（R12/R13）**：P1 允许 `test_impact` 缺省（默认 `indirect` 并提示），降低日常声明负担；`coverage-mismatch` 的证据链明确为**夜间全量/合并后 CI 记录**，绝不在轻量级 PR 门禁中制造误报，严格捍卫合入路径 ~2 分钟注意力预算；
* **G2 符号链接方向与双边可见（R19）**：固定 `CLAUDE.md → AGENTS.md` 单向链接并设写入防线；验收标准要求“Scoped 真身 + 根契约硬不变量”同时可见，堵住子目录丢失根契约的风险；
* **#855 与 #847 历史对账（R20/R21）**：正面回应了 2026-09-04 对“WIP 公告”的否决理由（自动化 vs 手工仪式、Advisory vs 锁定、多 Harness 实测成真），并确立了 #855 的三段解冻机制（Git merge=可引用 / Accepted=方向生效 / P0 完成=补全开工）；
* **R18 Competition 裁决收口**：在 Revisit 节明确将其裁决为“不入 Contract”，避免为了假想中的多 Agent 竞争场景增加系统复杂度。

---

## 四、 仅存的微小留痕（Non-blocking）

* **[`docs/adr/README.md`](file:///home/debian13/stability-test-platform/docs/adr/README.md)**：
  * 主表第 85 行已正确收录 ADR-0034 并标注为 `v0.4`；
  * 但下方的 `## Proposed 里程碑看板`（第 97 行）仍残留文字为 `ADR-0034（**Proposed** v0.3：多 Harness 执行契约，#855/#857）`，未同步更新为 `v0.4`。
  * *建议*：在 Accepted 转换或 P0 PR 中顺手同步。

---

## 五、 终审定论

**ADR-0034 v0.4 汇集了八方实测证据与严密的架构推演，无论是概念自洽性、文件系统原子性、状态机严谨性还是分期执行策略，均已无可挑剔。**

建议按以下顺序推进下一步：
1. **正式裁定 Accepted**：将 ADR-0034 状态转为 `Accepted`，同步 `docs/adr/README.md` 与里程碑看板；
2. **启动 Phase 0**：创建 `docs/development/ai/execution-contract.md`，平移细则并收缩 ADR 正文，完成共享元文件（`AGENTS.md` / `CLAUDE.md` / `repository-workflow.md` 等）的独立 docs PR 串行合入。
