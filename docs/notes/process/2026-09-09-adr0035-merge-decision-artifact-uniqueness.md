# ADR-0035 双份竞争提案合并 + 决策实体唯一性纪律（#906）

Status: implemented
Class: process

## Decision

1. **ADR-0035 合并为单一权威**：#1147（当前状态：接受共享 `AGENT_SECRET` + 升级
   触发 + 主机级方案骨架）与 #1163/#1170（目标形态：A 每主机凭据 + C 注册质询
   引入路径）是同一 Requirement（#906 / R02-R01）上两份独立提案，分歧点是**决策
   层级**（当前状态 vs 目标形态），不是结论对立。正文按四段化重组：§3 当前状态
   （立即生效，含威胁模型与冒充面收窄）/ §4 目标形态 / §5 迁移路径与实施骨架 /
   §6 升级触发条件；明确「**ADR Accepted ≠ 实施已启动**」，实施单另行拆分。
   原两份竞争文件（`ADR-0035-agent-secret-host-boundary.md` /
   `ADR-0035-agent-host-identity.md`）收敛为后者一份，ADR-0035 转 Accepted v1.1。
2. **契约 v1.8 新增 §3.5 竞争提案可见性与决策实体唯一性**：确立
   `N Harness → N Proposals → 1 Human Decision → 1 ADR` 分层——Execution ≠
   Artifact ≠ Decision；同一 Requirement 可有多个 Proposal Execution，但一个架构
   主题同一时刻只能有一个权威 Decision Artifact。强制纪律三条：①决策类 Execution
   必须显式 `declare --issue <n>`（让 §3.4 工作项查重真正生效）；②落笔写 ADR 前
   必须扫开放 PR 的同编号/同主题 ADR 提案；③同主题第二份权威 ADR 不得合入，只能
   作为 Proposal 交人类裁决后合并。
3. **S6 预算上调（仅 execution-contract.md）**：200 行/20KB → 260 行/26KB。
   依据：该文件是执行语义唯一权威源，v1.8 落地时 main 上已达 19638/20000 bytes
   （98%），预算已从「防臃肿」变成「阻止契约演进」；只抬该文件，其余预算不变。
4. **僵尸候选第二类 `closed-unmerged`（契约 v1.9）**：本次暴露——#1147 的 registry
   记录在 PR 关闭后仍是 `CODING + CLOSED`，按 §3.2 第三行**仍在风险窗口**，
   持续占用 issue #906 槽位与 scope，而唯一合法出口 `finish --abandon` 只能人工
   触发，无任何提示。裁决：**保留 CLOSED 在风险窗口的语义**（PR 被关 ≠ 工作停止，
   reopen/被取代都需要占位），但新增纯函数判据 `closed_unmerged_candidate`
   （integration=CLOSED ∧ lifecycle∈{CODING,FINISHED} ∧ STALE ∧ derived 为空），
   在 `status` 与 `drift` 显式提示，附两条出路（`finish --abandon` 出窗 /
   reopen·转手重新 declare）。**不改** `in_risk`、不自动出窗。
5. **CI 队首冲突容错（`scripts/ci/*.sh`）**：`reconcile-queue` 在队首 PR 与 main
   冲突时以 exit 1 结束，把红 X 刷到**每个** PR 的 checks 上（#1170/#1168/#1173
   实测同源）。冲突是可处置状态而非 job 故障，且 `reconcile-queue` 不在 required
   checks 内——两个脚本的 `update_branch_tolerant()` 增显式识别
   `Cannot update PR branch due to conflicts` 并绿退（原注释已提及该文案，代码却
   只靠「PR 已不在 open 态」兜底，而冲突 PR 仍 open → 落到 `return rc`）。

## Alternatives

- **`in_risk` 直接排除 CLOSED（自动出窗）**：一行改完即可释放 issue 槽位，但会把
  「误关/被取代/待 reopen」的记录移出风险窗口——两个 Execution 可能同时改同一批
  文件而无提示，违背 §3.2 第三行既有裁决；改为「保留语义 + 显式提示」。
- **在 `status` 里实时查 GitHub 判定 CLOSED**：`status` 严格只读且 P1 无心跳、
  网络不可靠（§3.3 降级条款），会把只读命令变成网络命令；改用已缓存的
  `integration_cache`，由 `update` 负责事实刷新。
- **`reconcile-queue` 冲突时保留非零退出**：保留红 X 会持续污染所有 PR 的信号，
  且该 check 不在 required checks 内、不阻塞合入，红 X 不带来额外保护；改为绿退 +
  明确指引。
- **保留两份 ADR-0035 或给 #1147 改号为 ADR-0036**：决策分层看似清晰，但同一
  #906 风险被拆到两份权威文档，读者需交叉阅读，且与 #1163 的分析大段重复；
  本次裁决取「一份 ADR、四个决策层级」。
- **立即实施主机级凭据**：泄露事件未发生、受信管理域假设仍成立，全网轮换 + 离线
  宽限的风险收益不匹配；保留为触发条件（§6）而非当前动作。
- **建 Decision Registry / ADR 文件锁 / 新增持久字段**：违反 §2.3「Registry 不对
  业务上锁」与选择权原则，且样本量=1；本次仅用既有 `--issue` 查重 + 可见性纪律，
  不引入新机制。
- **压缩契约既有章节腾出 §3.5 空间**：属与本任务无关的文档重构，违背
  AGENTS.md「只改当前 Requirement 必需内容」；改为按用户裁决上调该文件预算。

## Verification

- `python tools/dev/check_governance_surface.py --check` → 阻塞项全绿（S1–S12、
  S5x），含 S12 ADR 索引一致性（头部 `Accepted v1.1` ↔ adr/README 主表 ↔ DOC-MAP
  ↔ 版本记录块末项）与 S6 预算（契约 201 行/23596 bytes，在新 260/26000 内）；
- `python tools/dev/check_governance_surface.py --self-test` → 13 条规则红/绿双向
  自证通过（含 S6 行数/字节超限样例仍为红，证明上调预算未削弱检测能力）；
- `python tools/dev/ai_work.py --self-test` → 通过（新增 `closed_unmerged_candidate`
  六条红/绿断言：CODING/FINISHED+CLOSED+STALE+空 diff 为真；ABANDONED / LIVE /
  有落地 / MERGED / NO_PR 为假）；
- 实测 `ai_work.py status --id docs-906-agent-secret-boundary`：`update` 刷新
  integration 为 CLOSED 后，`finish --abandon` 使记录 `risk=no` 出窗；纯函数复核
  `issue_conflicts({906}, …) == []`、`in_risk("ABANDONED","CLOSED") is False`
  ——issue #906 槽位释放；
- `bash -n scripts/ci/pr-automerge-queue.sh` / `pr-update-branch-if-behind.sh` →
  语法通过；`grep -qiE "cannot update pr branch due to conflicts"` 对真实文案
  `X Cannot update PR branch due to conflicts` 与 `gh: … (HTTP 422)` 两形态命中；
- `ruff check backend/ tools/ scripts/` / `compileall` / `invariant-diff` /
  `immutability` / `pollution` / `ip-leak` → 全绿（见 PR 描述）；
- 事实核对：`gh pr view 1147/1163/1170`、`git merge-tree origin/main
  origin/docs/906-agent-secret-boundary`（确认冲突面为索引文件）、`ai_work.py
  status`（确认两记录 overlap 仅为 hint、第二条未声明 issue）；
- 手工确认 #1147 关闭前其 auto-merge 已解除（关闭后 `autoMergeRequest: null`）。

## Revisit

- 若再次出现同主题双份权威 ADR，升级为机械判据（例如决策类 Execution 的
  `--issue` 缺失即拒绝，或 scope 命中 `docs/adr/` 时把 overlap-hint 升为 WARN）；
- 触发条件：≥2 次同类事故，或决策类 Execution 在窗规模使人工扫开放 PR 不可靠；
- `closed-unmerged` 提示已落地但仍需人工动作（本契约不做自动出窗，理由见
  Alternatives）；若该类提示长期无人处置，再议是否把 `--abandon` 变为定时任务；
- S6 预算上调属一次性校准；若契约再次逼近 26KB，应优先讨论契约分层（细则分片）
  而不是继续抬预算；
- #906 实施单落地（§6 触发条件命中）时回链 ADR-0035 v1.1。
