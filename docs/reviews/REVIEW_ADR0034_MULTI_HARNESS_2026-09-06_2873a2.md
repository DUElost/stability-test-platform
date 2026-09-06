# 只读审查报告：ADR-0034 多 Harness 并行执行契约（v0.3）

- 审查日期：2026-09-06
- 审查对象（只读，未修改任何文件）：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.3）
  - 差异基线 `a6869878..55e9a71e`（v0.2 → v0.3，含 README 索引同步 #861）
  - 关联：`docs/notes/process/2026-09-06-adr-0034-draft.md`、`docs/development/ai/harness-adapters.md`、`docs/development/repository-workflow.md`、`tools/dev/check_governance_surface.py`
- 审查方式：对照仓库实态逐条核验（git 行为实测、门禁实跑、接线面 grep），并追踪 v0.2 轮次遗留项的处置情况
- 仓库状态：HEAD `55e9a71e`，工作树干净；治理门禁 `S1–S11、S5x` 全绿

---

## 一、结论摘要

v0.3 的方向正确，且本版最有价值的改动是它自己发现的语义错误：**overlap 集合不能只看 liveness**。v0.2 的「仅 `ACTIVE` 记录参与 overlap 检测」在「Execution A `finish` 后 Harness 退出转 STALE、但其 PR 仍在改 `foo.py`」的场景下会漏报，v0.3 拆成 liveness × integration 两维正交、把 overlap 集合定义为 `integration ∈ {NO_PR, PR_OPEN, READY}` 是正确修正。配套的三项收敛——STALE 永远 advisory、TTL 分期（P1 advisory / P2 升格）、MERGED 权威归 GitHub——方向一致，都是让 Registry 不僭越权威，与 §2.4「diff 优先」一脉相承。

**发现 1 处硬缺陷（H1，实测证据见下）、1 处设计闭环缺口（H2）、4 处中等问题（M1–M4）、2 项上一轮遗留未处理（R1–R2）。** 均不涉及架构改造，建议在 Accepted 前修订。

---

## 二、v0.3 已修复的上一轮问题

| 上一轮发现 | 处置 |
|---|---|
| §2.2 registry 位置自相矛盾（「落所有 worktree 之外」vs「主 checkout 固定绝对路径」+「入 `.gitignore`」） | 已修：改为 `$(git rev-parse --git-common-dir)/ai-work/`，并说明位于 `.git` 内天然不被跟踪。方向正确，但命令本身有实现陷阱，见 H1 |
| `docs/adr/README.md` M7 行版本漂移（写 v0.1，本体已 v0.2） | 已修（#861），现同步为 v0.3 |

---

## 三、高优先级发现

### H1. `git rev-parse --git-common-dir` 返回相对路径，且随 cwd 变化

§2.2 把这条命令写成了契约字面（"Registry root = `$(git rev-parse --git-common-dir)/ai-work/`"），但其输出是 cwd 相关的。本机实测：

| 运行位置 | 输出 |
|---|---|
| 主 checkout 根 | `.git` |
| 主 checkout 子目录（`backend/agent/`） | `../../.git` |
| linked worktree（`/tmp` 下一次性 worktree） | `/home/debian13/stability-test-platform/.git` |

只有 linked worktree 一路返回绝对路径。实现若按「取输出、拼 `/ai-work/`」处理，在主 checkout 内从任意子目录调用 `ai_work` 都会解析到不存在的位置——而从深层目录启动恰恰是本契约声明的常态场景（§1：「多 Harness 执行恰以深层目录为常态」）。

**建议**：契约写成 `git rev-parse --path-format=absolute --git-common-dir`（要求 git ≥ 2.31），或明确要求对输出做 `realpath`；并在 §5 的 P1 验收里把「主 checkout 根 / 主 checkout 子目录 / linked worktree 三处解析到同一绝对路径」列为红绿样例。

### H2. `READY → MERGED` 没有执行者，overlap 集合只增不减

三条规定各自成立，合起来留下闭环缺口：

- §2.3：`ai_work` 不得单方面写 `MERGED`，`update` 落终态前必须核对 GitHub PR 状态；
- §2.5：P1 无 heartbeat daemon，TTL 仅 advisory，**不自动改写任何字段**；
- §2.7 P1 交付物：未包含任何 GitHub 查询能力（无 `gh` 依赖、无 PR 状态同步）。

结果是记录进入 `READY` 后没有任何机制把它移出 overlap 集合，而 overlap 集合恰恰包含 `READY`。overlap 提示会单调累积，很快因噪音被忽略，Registry 的唯一产出随之失效。这与 P3 drift gate 的 advisory 定位叠加后风险更高：两层都是 advisory，都靠人读，噪音是共享成本。

**建议**：在 P1 明确二选一并写进交付物——(a) `update` 子命令带 `gh pr view` 查询（则 P1 获得 gh 与网络依赖，自测样例需覆盖 gh 不可用的降级路径）；(b) 显式声明 P1 靠人工 `update` 收口，并承认集合会积压陈旧项、由 `status` 输出提示。现文本两者皆未表态。

---

## 四、中等问题

### M1. P0 接线清单仍漏 `docs/development/repository-workflow.md`

上一轮已提出，v0.3 未处置。§2.7 P0 列出的指针接线对象是「`harness-adapters.md` 与 Phase -1 基线 note」，但 `repository-workflow.md` 才是 `AGENTS.md:39` 直接指向的并行约定入口，其 §并行 worktree（第 19–21 行）与结尾（第 39 行）两处都写着「以 2026-09-04 note 为准 / 直至后续 ADR 正式取代」。v0.3 给 P0 又加了「建立 `execution-contract.md` 并完成最小引用接线」，接线面比 v0.2 更大，此遗漏更值得补。

### M2. `execution-contract.md` 未纳入治理门禁清单

§2.10 指定 `docs/development/ai/execution-contract.md` 为 Execution Contract 唯一权威源，P0 负责建立它。但 `tools/dev/check_governance_surface.py` 中：

- S2 的 `link_files` 未包含该路径（同目录的 `harness-adapters.md` 已在）；
- S6 的 `RESIDENT_BUDGETS` 未包含该路径（`harness-adapters.md` 有 100 行 / 10000 字节预算）。

§3 为 G2 迁移明确写了「checker 同步：S6 预算表加 scoped AGENTS.md 条目；S2 `link_files` 加新路径」，P0 却无对应项。一份被指定为唯一权威源的文档不受断链与体量门禁保护，正是这套治理面设计要防的形态。

### M3. §2.9 `test_impact` 在 P1 入 schema、P3 才有消费者

字段在 P1 落 registry schema，coverage-mismatch 检测允许后置至 P3。中间 P1、P2 两期该字段只写不读，期间没有任何机制能验证 Harness 是否正确填写；等 P3 真正需要时，字段里可能已积累两期的噪音数据。ADR 自身在 §2.8 立了「Contract 即约束，不留给实现自由解释」的规矩，本节属擦边。不是错误，是取舍，但建议评估「字段与检测一并推到 P3」是否更干净。

### M4. 「五处 canonical」与「唯一权威源」措辞冲突

§2.7 P0 写「规则单一权威源（AGENTS.md/CLAUDE.md/.cursor/.codex/docs 五处 canonical + 最小引用）」，§2.10 写 `execution-contract.md` 为 single authority、其余入口只保留最小引用与指针。两种说法并存会让 P0 实施者对「哪些位置可以写执行语义」产生分歧。建议统一为：执行语义唯一权威源 = `execution-contract.md`；其余五处均为最小引用，不得复制语义。

---

## 五、上一轮遗留、v0.3 未处理

### R1. P1 缺启动判据，且 v0.3 加重了这一失衡

§1 的论据是「多 Harness 已实测可用」（附录 A 矩阵），§4 否决「维持 2026-09-04 约定」的理由却是「多 Harness **常态化**后冲突窗口从同会话变跨 Harness」。可用 ≠ 常态化——审查时 `git worktree list` 仍只有主 checkout 一个。P4 有具体启用条件（「人已难判集成顺序」真实积累后），P1/P3 没有对等门槛。v0.3 反而给 P1 追加了交付物（scope MVP 校验、`test_impact` schema、registry root 实测），启动门槛的缺失更突出。

**建议**：给 P1 补一条与 P4 同样具体的触发判据（例如「连续两周并行 worktree ≥3」或「实际发生 N 次跨 Harness 撞车返工」）。

### R2. §2.6 并发上限的理由改写，但未正面回答原结论

新表述为「逐 PR 决策已政策化给 auto-merge（approvals=0 + FIFO），人的注意力从『审 PR』转移到『审审计面』——瓶颈仍是人的吞吐，上限只是换了守的对象」。`2026-09-04-multi-agent-parallel-convention.md` 的原结论（瓶颈 = 审阅吞吐；N 增大后加协同机制不提升吞吐，正确做法是任务排队）并未被证伪。ADR 承认瓶颈未变，但未解释在瓶颈未变的前提下、增加一个需要人读的审计面为何是净收益。这是 supersede 关系中最需要正面回应的一条。

---

## 六、验证记录

| 项 | 命令 / 方式 | 结果 |
|---|---|---|
| git-common-dir 三位置行为 | 主 checkout 根 / 子目录 / `/tmp` 一次性 linked worktree 实跑，测后已 `worktree remove` + `branch -D` 清理 | 见 H1 表；仓库恢复单 worktree |
| 治理门禁 | `venv/bin/python tools/dev/check_governance_surface.py --check` | `[OK] S1–S11、S5x` 全绿 |
| 门禁自测 | `venv/bin/python tools/dev/check_governance_surface.py --self-test` | `[OK] 12 条规则红/绿双向` |
| `execution-contract.md` 现状 | `ls docs/development/ai/` | 仅 `harness-adapters.md`；契约文档尚未建立（符合 P0 在 Accepted 之后的安排） |
| S2 / S6 覆盖面 | `grep -n execution-contract tools/dev/check_governance_surface.py` | 零命中（M2 依据） |
| 工作树 | `git status --short` | 干净（本报告为新增未跟踪文件） |

未运行项（pending）：ADR 为纯文档变更，未跑 `backend/tests`、vitest 与 PR CI；六项 required checks 以实际运行为准。

---

## 七、建议处置顺序

1. **H1**：修正 registry root 的解析方式并补三位置验收样例——这条会直接决定 P1 实现是否可用。
2. **H2**：在 P1 交付物中就 `READY → MERGED` 的执行者表态（gh 集成或人工收口二选一）。
3. **M1 / M2**：补 P0 接线清单的 `repository-workflow.md`，并把 `execution-contract.md` 加入 S2 `link_files` 与 S6 `RESIDENT_BUDGETS`。
4. **M4**：统一「唯一权威源」措辞。
5. **R1**：给 P1 补启动判据。
6. **M3 / R2**：作者判断后回应即可，不阻断 Accepted。
