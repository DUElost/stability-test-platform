# ADR-0034 多 Harness 执行契约只读审查报告

- 审查类型：系统性只读审查与架构审计（未修改任何生产代码）
- 审查对象：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.3 → v0.4 → v0.5 → Accepted v1.0 → v1.1 → v1.2）
  - `docs/development/ai/execution-contract.md`（Living v1.1，Execution Contract 唯一权威源）
  - `docs/reviews/REVIEW_ADR0034_MULTI_HARNESS_2026-09-06_synthesis.md`（八源审查综合裁决，R1–R30 权威映射）
  - `docs/notes/process/2026-09-06-adr-0034-draft.md`（起草与修订历程）
  - `docs/notes/feature/2026-09-07-*.md` / `docs/notes/bug-fix/2026-09-07-*.md`
  - `tools/dev/ai_work.py` 及 `.git/ai-work/registry.yaml`
- 会话 Resume 标识：`aa114c`（Conversation ID: `2357bf61-1c1b-4139-b5be-378318aa114c`）

---

# 第 1 轮：ADR-0034 v0.4 综合审查（八源 synthesis 与裁决落地）

## 一、 审查结论与总体定性

**ADR-0034 v0.4 无阻断性缺陷（No Blockers），无 A 级缺陷。契约完备度极高，边界定义已无任何模糊死角，建议正式进入 Accepted 状态。**

从 v0.3 到 v0.4，文档系统性地吸收了同日落库的 8 份独立只读审查成果，经过综合裁决（`synthesis.md`，R1–R22 权威编号映射），一次性落地了 **21 项精确采纳** 与 **2 项关键人工裁决**（R6 状态模型实现属性澄清、R18 竞争模式裁决不入）。它终结了“状态机如何闭环”、“多 Worktree 解析一致性”、“真实 Diff 偏离声明”、“过早过度设计”等全部争议点，完全具备作为跨 Harness 唯一执行基准的严肃性与可行性。

## 二、 门禁与机械校验记录

在 commit `1715eee6` 代码基座上执行质量与治理结构门禁：

| 门禁 / 校验项 | 实际运行命令 | 校验结果 | 备注 |
|---|---|---|---|
| **快速静态门禁（6 gates）** | `venv/bin/python scripts/run_gates.py check:quick` | **PASS (0)** | ruff、eslint、tsc、knip、compileall、gov-surface 全绿（耗时 ~19s） |
| **治理面结构门禁（L0）** | `python3 tools/dev/check_governance_surface.py --check` | **PASS (0)** | S1–S11、S5x 阻塞项全部通过 |
| **治理面红绿双向自测** | `python3 tools/dev/check_governance_surface.py --self-test` | **PASS (0)** | 12 条规则的正反样例双向验证全部通过 |
| **常驻入口预算控制（S6）** | `check_governance_surface.py:check_resident_budgets` | **PASS** | `AGENTS.md` 64 行 / 3815B（≤80行/8KB）；`CLAUDE.md` 27 行 / 978B（≤60行/6KB） |
| **硬不变量在场防护（S11）** | `check_governance_surface.py:check_hard_invariant_anchors` | **PASS** | 11 条原文正则锚点对当前 `AGENTS.md` 100% 命中 |
| **README 主表收录（R14）** | 检查 `docs/adr/README.md:85` | **PASS** | ADR-0034 已正式收录进 `## 当前 ADR 清单` 主表（行 85） |
| **DOC-MAP 架构索引（R14）** | 检查 `docs/DOC-MAP.md:83` | **PASS** | 架构 ADR 列表已正式链接 ADR-0034 及 `synthesis.md` |

## 三、 v0.4 关键加固与 R1–R22 裁决逐项核验

1. **状态模型三维正交与语义闭环（R2, R3, R4, R6）**：
   - R6 裁决背景明确：三维是解决 `finish` 缺少独立可持久化语义表达、兼载中途放弃的**合规实现选择，而非冻结条款**。
   - `lifecycle ∈ {CODING, FINISHED, ABANDONED}`（执行侧自声明；`ABANDONED` 严格显式人工）；`liveness ∈ {LIVE, STALE}`（永远纯为 Advisory、查询时派生不持久化）；`integration ∈ {NO_PR, PR_OPEN, READY, MERGED, CLOSED}`（GitHub 权威；READY checks 派生刷新可回退）。
   - R4 观察者语义守卫：`status` 命令**严格只读**，不改变被观察对象的 `last_seen`。
2. **存储拓扑与原子写九步全序（R1, R7）**：
   - R1 唯一解析发现方式：固定使用 `$(git rev-parse --path-format=absolute --git-common-dir)/ai-work/`，按 Clone 物理隔离。
   - R7 原子写九步全序：补齐 `parent directory fsync`，封死目录项未刷盘的文件丢失漏洞。
3. **Overlap 事实优先与真实反例对账（R5）**：
   - 数据源合成公式：`overlap 数据源 = declared ∪ derived(diff)`，Diff 优先，引用 2026-09-04 真实事故反例为凭。
4. **治理架构与分期边界防护（R8, R9, R10, R11, R22）**：
   - R8/R9 单一规范权威源：P0 细则一次性平移入 `execution-contract.md`，ADR 收缩为决策要点；
   - R10/R11 薄入口接线并纳入 S2/S6 门禁覆盖；
   - R22 P1 启动判据增设理性门槛（“连续两周并行 worktree ≥3，或实际发生 ≥2 次跨 Harness 撞车返工”）。
5. **质量纪律与周边规则闭环（R12, R13, R17, R18, R19, R20, R21）**：
   - Scope 拒绝规则（R17）；`test_impact` 缺省策略（R12）；`coverage-mismatch` 绑定夜间全量证据捍卫 2 分钟注意力预算（R13）；
   - G2 符号链接写入防线与双边可见（R19）；#855 三段解冻机制（R20）；#847 对账（R21）；R18 竞争模式裁决不入 Contract。

---

# 第 2 轮：ADR-0034 v0.5 只读审查（第二轮八源复审加固）

## 一、 总体审查结论与定性

**臻于完善，逻辑推演与工程防线已完全闭环，强烈建议正式裁定 Accepted。**

v0.5 在 v0.4 的基础上吸收第二轮 8 源复审（R23–R30），消除了两个隐蔽死角：
1. 状态机中 `ABANDONED × PR_OPEN` 悬空组合导致的**开放 PR 风险窗口泄漏**；
2. Effective Scope 定义中“并集 vs 冲突以 diff 为准”的自相矛盾表述与新建未跟踪文件漏检。

## 二、 v0.5 八项核心采纳（R23–R30）逐条专项核验

1. **Overlap 集合重构为严密真值表（R23，§2.3）**：
   ```text
   risk = integration ∈ {PR_OPEN, READY}                                ← GitHub 事实：开放 PR 恒在窗口
       OR (integration = NO_PR    AND lifecycle ∈ {CODING, FINISHED})   ← 无 PR 但执行侧未放弃
       OR (integration = CLOSED   AND lifecycle ≠ ABANDONED)            ← PR 被关 ≠ 工作停止（误关/reopen）
   ```
   开放 PR 永远由 GitHub 状态裁决，执行侧单方面 abandon 绝不能遮蔽代码合并风险。`finish --abandon` 仅在无开放 PR 时方可立即出窗；有开放 PR 时告警并强制留窗，直至 GitHub 侧终态。
2. **Effective Scope 确立“并集恒成立”与三级回落（R24 & R25，§2.2）**：
   - 统一确立为 `effective scope = normalized(declared) ∪ derived(diff)`，偏离时输出 Advisory Drift 提示；
   - 引入 `git ls-files --others --exclude-standard` 抓取新建的 untracked 文件，堵住新创文件冲突盲区；
   - 建立三级回落顺序：`工作树当前 diff（含 untracked） → 分支比较 diff（merge-base..branch） → 声明兜底`，支持工作树删除后的风险追踪。
3. **#847 头部对账修正（R26）**：
   - 移除“工具自动登记”的不准确措辞，将对账立足于 Advisory/Visibility-only + `declared ∪ derived` 对过期信任的化解 + 多 Harness 并发实测成真。
4. **措辞同步与物理认知纠偏（R27）**：
   - 纠正“symlink 天然防误写”误区，明确指出符号链接不提供写保护，需靠规约与门禁约束；
   - P0 必备目录增加 `lifecycle`（执行模型）与 `pipeline_def.lifecycle`（S11 锚定词）的消歧要求；
   - 附录 A 正式将 `Antigravity CLI` 补录为“未验证（延期）”，维护证据纯洁性；
   - `docs/adr/README.md` 主表（行 85）与看板（行 97）双行彻底同步为 `v0.5`。
5. **治理平移标准与负载拆分（R28 & R29，§2.7 P0）**：
   - 细则平移时 ADR 升 v1.1 并做修订留痕，严守“Accepted 记录不可被无痕改写”规范；
   - 采纳建议将 P0 拆分为两个 PR：**P0a（契约主体）** 与 **P0b（接线/门禁/supersede）**。
6. **P1 启动过渡期确认（R30，§2.7 P1）**：
   - 明确在 P1 启动判据触发前，继续保持 2026-09-04 派生视图实践，平稳过渡。

---

# 第 3 轮：ADR-0034 开发工作完结只读确认（2026-09-07）

## 一、 核心结论

**ADR-0034 全部分期开发工作已正式全部完结（100% 交付收口）。**

主干代码与提交历史证实：ADR-0034 规划的所有必须开发期（**P0a/P0b、G2 试点、P1、P2/P2b、P3**）已全量通过独立 PR 合并入 `main`；唯一的 **P4**（Integration Planner）按契约定义为“按需观察项，暂不开发”。整个多 Harness 并行执行基础设施已处于完全可用且受门禁保护的状态。

## 二、 各分期交付总台账

| 阶段 | 交付核心内容 | 对应 PR / Commit | 状态 |
|---|---|---|---|
| **ADR 定稿** | ADR-0034 正式 Accepted（v1.0 → v1.1 细则迁出 → v1.2 启动判据增补） | PR #865, #866, #877 | **已闭环 (Accepted)** |
| **P0a** | 建立执行契约唯一权威源 `execution-contract.md`，ADR §2 细则一次性外迁 | PR #866 (`a3fdb0d5`) | **已交付** |
| **P0b** | 薄入口接线（`AGENTS.md`, `CLAUDE.md`, `repository-workflow.md`），09-04 note supersede 标注，纳入 S2/S6 门禁 | PR #869 (`9fa443f4`)<br>PR #874 (`64c21014`) | **已交付** |
| **G2 试点** | Scoped `AGENTS.md` 真身 + `CLAUDE.md` symlink 薄壳（`backend/agent/` 及 `aee/`）；4 家 Harness 探针验证 4/4 全绿 | PR #870 (`b7f569e7`)<br>PR #873, #875 | **已交付** |
| **P1** | **Execution Registry MVP**：`tools/dev/ai_work.py` 落地（`declare/status/update/finish --abandon`、九步原子写、三维正交模型、真值表 overlap 检测、未跟踪文件三级回落） | PR #878 (`5dc4c3bd`) | **已交付** |
| **P2 / P2b** | **Harness Adapter 与上下文供给**：`ai_work whoami` 命令与心跳指引；Claude 根 bootstrap 脚本注入（`tools/dev/claude_wrapper.sh`）及加载矩阵终验 | PR #879 (`ffd70b27`)<br>PR #892 (`7ffa4d05`) | **已交付** |
| **P3** | **Drift Gate（ADR-0034 最后一块拼图）**：`ai_work drift` 命令（freshness 告警、declaration-drift 双清单、coverage-mismatch MVP、overlap 顶层目录 hint）；接入 `run_gates` 之 `check:full` | PR #893 (`690040da`) | **已交付** |
| **P4** | **Integration Planner**：仅在“人已难判集成顺序”真实积累后启用 | — | **观察项（无需开发）** |

## 三、 关联 Issue 闭环状态

1. **#854（治理门禁逃逸向量）**：由 PR #876（commit `af451ca1`）全部修复，S9 深层标题、S10 非日期文件名逃逸被拦截，self-test 全量覆盖（Closed）；
2. **#857（Claude 子目录 import 解析缺陷）**：通过 G2 symlink 形态与 P2b `claude_wrapper.sh` 根上下文供给在仓库层面彻底规避并通过实测矩阵；
3. **#855（行为验证缺口）**：P0 完成达成解冻条件，P1/P3 将测试路径与 coverage-mismatch 纳入静态 drift 检查，残余作为长期技术债跟踪（Deferred，不阻塞 ADR 交付）；
4. **#825（门禁覆盖度对齐）**：由 PR #895（commit `0bed0db9`）完成修复并合入主干（Fixed）。

---

# 第 4 轮：多 Harness 批次第一单 #880 修复与 Dogfood 只读确认（2026-09-07）

## 一、 核心事实确认

**通报事实 100% 核实吻合，无任何偏差。**

1. **主干基座**：`main` 分支最新 commit 为 `3e072f62`（`Merge pull request #899 from DUElost/fix/880-registry-codec`），主工作树状态干净（Clean）。
2. **Issue 闭环**：[#880](https://github.com/DUElost/stability-test-platform/issues/880) 已由 PR #899 的 `Fixes #880` 关联**自动置为 CLOSED 状态**，Milestone 为 `P0 - 核心执行力与平台闭环`。
3. **PR 合入记录**：PR #899 包含 4 个 commits，CI 全部绿灯合入（含 CodeQL 安全修复）。
4. **Dogfood 留痕**：真实 Registry（`.git/ai-work/registry.yaml`）中已完整沉淀两单真实生命周期记录（#825 与 #880），状态机 T6 及真值表出窗机制实战运转正常。

## 二、 修复实现与防御深度核验

对照 `tools/dev/ai_work.py` 与 Agent Note `docs/notes/bug-fix/2026-09-07-ai-work-registry-codec-880.md`，#880 建议 1+2+3 全部高质量落地：

| 修复层面 | 落地位置 | 实现核验细节 |
|---|---|---|
| **1. 语义层守门** | `ai_work.py:352-366` | 新增 `normalize_requirement_id`：拦截 `#` 开头与含 `:` 的 id，直接输出 `[REFUSED]` 并带改名指引（推荐 `issue-878` 式 slug），阻止坏 key 落盘。 |
| **2. Codec 对称化** | `ai_work.py:69-75` | `_quote` 严格将 `#` 从免引号字符集中剔除（含 `#` 必加引号，防行首裸 `#` 被 YAML 视作注释）；`_unquote` 正确支持受限转义。 |
| **CodeQL ReDoS 免疫** | `ai_work.py:115` | 正则重构为非重叠交替结构 `^(?:"((?:[^"\\]|\\.)*)"|(\S[^:]*)):$`，根除了 `(\\.|[^"])*` 的指数级回溯隐患（commit `6ce81f80`）。 |
| **3. 损坏隔离兜底** | `ai_work.py:201-216` | 落实契约 §2.2：`read_registry` 遇 `ValueError` 自动将坏文件隔离为 `registry.yaml.corrupt-<时间戳>` 留证，输出人类可读的重建与恢复指引，彻底消除“一损俱损、全命令瘫痪”的死锁。 |
| **4. 残留 .tmp 清理** | `ai_work.py:258-264` | `load_locked` 持锁后自动检查并清除前次异常崩溃遗留的无主 `.tmp` 文件。 |

## 三、 实战 Dogfood 状态与 Registry 物理验证

通过只读执行 `venv/bin/python tools/dev/ai_work.py status` 与检查 `.git/ai-work/registry.yaml`，确认：

```text
fix-825-gates-parity: claude lifecycle=FINISHED liveness=LIVE integration=MERGED risk=no
  effective_scope=['scripts/run_gates.py', 'tools/dev/check_governance_surface.py', 'tools/dev/check_pr_migrate.py']
fix-880-registry-codec: claude lifecycle=FINISHED liveness=LIVE integration=MERGED risk=no
  effective_scope=['docs/notes/bug-fix', 'tools/dev/ai_work.py']
```

- **生命周期闭环**：`declare → whoami → update --pr 899 → READY → merge → update reconcile → MERGED` 全链路实跑验证通过；
- **真值表出窗验证**：两单均进入 `integration=MERGED`，真值表正确判断为 `risk=no`，无残留风险窗口；
- **自测套件验证**：`venv/bin/python tools/dev/ai_work.py --self-test` **全绿通过**（包含 #880 三缺口红绿样例与首版 `#broken` 标量样例修正）。

## 四、 审查总结与后续批次就绪态

#880 的发现与修复是 Execution Registry 基础设施第一次极其成功的真实 Dogfood 实战。它在多 Harness 批次全面铺开前，以最高优先级扫清了“工具链自身崩溃与锁死”的 P0 级风险，补齐了契约要求的损坏隔离与残留清理兜底，并且成功经受住了 CodeQL 严格的安全审计。

目前，**主干干净、工具链健壮、首单闭环**，多 Harness 批次开发基础设施已全面具备高吞吐实战承接能力。
