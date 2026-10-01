# 仓库开发与集成工作流

本文记录非代码交付物、并行 worktree 和 PR/CI 集成规则。命令与测试见
[`local-development.md`](./local-development.md) 和 [`testing.md`](./testing.md)。

## Agent Note

每个非平凡变更必须随 PR 附带或更新一条 Agent Note：

```text
docs/notes/{feature|bug-fix|simplification|architecture|process|testing}/yyyy-mm-dd-主题.md
```

记录 Decision、Alternatives、Verification、Revisit。纯机械改动豁免；方向级决策使用
ADR。模板与判定见 [`docs/notes/README.md`](../notes/README.md)。

## 并行 worktree

并行执行语义的权威源是
[`ADR-0034`](../adr/ADR-0034-multi-harness-execution-contract.md)（Accepted）与
[`execution-contract.md`](ai/execution-contract.md)。Execution Registry（P1，
`tools/dev/ai_work.py`）已落地并被采用——契约 §9 启动判据第 1 条（已计划的
多 Harness 批次启动前预置就绪）已触发，**现行操作规范为契约正文协议**：
所有实施者开工先查开放 PR / 远端分支；仅协调域内再 `ai_work.py status --risk`
前检 + `declare`，收尾 `finish` 与 GitHub reconcile。域外 M2 不写 Registry（契约 §3.6）；
派生视图降为 ground truth 交叉验证手段（契约 §9）。
下列规则（源自
[`2026-09-04-multi-agent-parallel-convention.md`](../notes/process/2026-09-04-multi-agent-parallel-convention.md)，
其并行语义已被 ADR-0034 取代）继续有效：

- 冲突靠开工前开放 PR / 远端分支检查、协调域内 Registry 前检与实际 diff 交叉验证避免，不依赖手写 WIP 状态；
- 分片只用于冲突规避，不形成目录所有权；
- `AGENTS.md`、`CLAUDE.md` 及 Harness 共享规则同一时间只由一个工作面修改（协调域内经 Registry、
  协调域外查开放 PR 串行）；
- Registry 只登记其协调域内的实施者（现阶段即本机同一克隆内的 Harness 会话）；本地领单前
  先查开放 PR，再 `status --risk` / `declare`。协调域外实施、规划 / 复核工作面不 declare，
  写仓库前查开放 PR / 远端分支；M2 云端实施与本地在窗 Execution 可能重叠时默认不并发，
  Owner 对照本地 `status --risk`，无重叠可并行，共享元文件串行（契约 §3.6，ADR-0058 D10）；
- 并发不设会话数上限（ADR-0034 §2.6 v1.8）；瓶颈在集成收尾侧（审阅吞吐 + 平台可靠性），在窗 Execution 规模与 reconcile 负载为实测代理，恶化时重议。

派生视图（交叉验证；`effective_scope = declared ∪ derived` 中 derived 是 Git
事实、声明不能覆盖，契约 §5.1）：

```bash
for w in $(git worktree list --porcelain | awk '/^worktree /{print $2}'); do
  printf '%-46s -> ' "${w##*/}"
  git -C "$w" diff --name-only "$(git -C "$w" merge-base origin/main HEAD)" \
    | cut -d/ -f1 | sort -u | paste -sd, -
  printf '\n'
done
```

Registry 细则（写入协议、scope 语法与 overlap 谓词、三维状态、drift、reconcile）
见 `execution-contract.md` §2–§7。

## Registry 批次收窗与 drift 留痕（#1097）

`ai_work.py drift`（run_gates `ai-drift` gate，advisory 不阻塞）只在 registry
宿主机产生信号（`check:full` 或按需运行）；CI runner 无 registry 数据，接
PR/CI 恒 no-op，故**不接入**，留痕靠下述收窗纪律（论证见 issue #1097）：

- **先批量核销再收窗（#1234）**：`python tools/dev/ai_work.py update --all`
  ——一次 gh 调用刷新已登记 PR 的终态（MERGED/CLOSED），一次九步写落盘，
  **不刷任何 `last_seen`**（无 execution identity 即不冒充心跳）。逐条 `update`
  刷上百条记录是分钟级；批量是秒级，且能一次清掉「已合入但缓存仍称开放」的
  陈旧记录（实测 22 条一次清空）。
  **覆盖面边界**：批量路径只取 GitHub 最新 400 个 PR——更早合入的记录不会被刷到，
  用单条 `python tools/dev/ai_work.py update --id <requirement>` 兜底核销
  （`status` 对这类记录会标 `stale-cache` 并提示 `update --id`）；
- 批次收窗与合入核销/reconcile 前先跑 `python tools/dev/ai_work.py drift
  --strict`；有提示先处置再收尾——STALE 记录人工裁决、declaration-drift
  补/收窄 scope、overlap 改串行；
- 开工前检看风险面用 `python tools/dev/ai_work.py status --risk`（只列在窗
  记录 + overlap 提示；全量仍用不带 `--risk` 的 `status`）；
- 输出与处置结论随批次收尾评论留痕（#1035 Evidence 台账回溯组同载体）；
- 一页速查（registry 记录 / worktree / 本地与远端分支四类判据）见
  [`closure-cheatsheet.md`](./closure-cheatsheet.md)——**不新增规范**，冲突以本文与
  [`ai/execution-contract.md`](ai/execution-contract.md) 为准。

## worktree 与本地分支清理

**前置（顺序固定）**：先按上节「Registry 批次收窗」跑 `update --all`（更早记录用单条
`update --id` 兜底）与 `drift --strict`，再动本机资源——顺序反了会把「已合入」误判成
待清理。四层判据的一页速查见 [`closure-cheatsheet.md`](./closure-cheatsheet.md)。

### worktree

| 判定 | 条件 | 动作 |
|---|---|---|
| 可删 | 分支 patch 全在 `origin/main` **且** `git -C <worktree> status --porcelain` 为空 **且** 对应 Execution `lifecycle=FINISHED` | `git worktree remove <path>`（**不加** `--force`） |
| 保留 | Execution 仍 `CODING`（即使 PR 已合入）/ 有任何未提交改动（含未跟踪文件）/ registry 无记录（多为别家 harness 的 scratch，交用户裁决）/ main worktree 里他人的文件 | 不动 |

- 移除后 `git worktree prune -v`；移除 worktree **不删分支**，需要时
  `git worktree add <path> <branch>` 重建；
- **每次清理前重查列表**（别家会话可能刚新建）；`/tmp/stp-*` 这类无 `.git` 的 scratch
  目录逐文件定性后再删（2026-09-15 实测：4 个目录里藏着 2 份**未落地草稿**——
  「scratch」≠ 可删）。

### 本地分支

| 判定 | 判据 | 动作 |
|---|---|---|
| 安全集 | `git for-each-ref --merged origin/main refs/heads` | 可删；**不能**拿 `git branch -d` 的默认判定当安全网（本地 `main` 常落后，落后量不定） |
| 直删 | `git cherry origin/main <branch>` 的 `+` 行数 = 0（rebase 合入 patch-id 不变 / 合并后同步） | 可删 |
| 人工核 | `+` > 0（squash/amend 改写了 patch-id，或真未合） | `gh pr list --state merged --head <branch>` 核 PR 已 merged + 内容等价 ⇒ 可删；否则保留 |
| 备份 ref | 仅「PR MERGED 但 patch 改写」 | `git update-ref refs/backup/<date>/<branch> <sha>` 后再删 |
| 保留 | `main` / 被 worktree 占用（`git branch -vv` 行首 `+`）/ registry 仍 `CODING` | 不动 |

收尾 `git fetch --prune origin`。远端侧见下节（共享态，动作前须人工确认）。

## 远端分支生命周期与补删

**基线：合入即删由仓库设置承载**——`delete_branch_on_merge=true`（GitHub 侧），PR 合入时
自动删头分支，无需人工动作（2026-09-20 实测：一日内 5 个 PR 合入后远端分支自动消失）。

**三种需要「补删」的例外**：① 合入后被继续推「同步 main」提交的分支会**复活**；
② rebase/改写合入的分支（patch-id 与主干不同）自动删可能漏；③ **关闭未合**的 PR 分支
不会被自动删。收窗时按下列判据批量核一遍：

1. `git fetch --prune origin`，取 `git branch -r --no-merged origin/main`；
2. 逐个 `git cherry origin/main <branch>` 判内容是否已在主干：
   - `+` 数 = 0 ⇒ 内容已在主干（合并后同步提交 / **rebase 合入**——patch-id 不变）→ **删**；
   - `+` > 0 且 `gh pr list --state merged --head <branch>` 显示 PR 已合入、且逐文件核对
     主干已含**等价改动** → **删**（squash/amend 等改写合入会改 patch-id，`+` 不为 0，
     故不能只看 `+`）；
   - `+` > 0 且 PR 为 CLOSED、关闭评论写明**被同修 PR 取代**、且逐文件核对主干已含
     等价改动 → **删**（2026-09-20 实例：`fix/2706-…`，其唯一补丁的候选集修复已由
     #2708 落进 `tools/site_config/handover.py`）；
   - `+` > 0 且无取代 → **保留**；确要删则先打 `refs/backup/<date>/<branch>` 备份 ref
     （与本地改写合入同款保护），并在删除说明里留判据；
3. **保留面**：`main`、open PR 的头分支、registry 中仍 `CODING` 的 Execution 分支、
   未合并且未被取代的 WIP——四类一律不动；
4. **执行纪律**：远端删除是**共享态动作**——先出「清单 + 逐条判据」交人工确认，再
   `git push origin --delete <branch>` 批量执行，最后 `git fetch --prune` 收尾。
   本地分支与 worktree 按上一节[「worktree 与本地分支清理」](#worktree-与本地分支清理)
   处置，两侧判据不混用。

**与「关闭未合」记录的关系**：PR 被关闭而未放弃的 Execution 仍在 registry 风险窗口
（僵尸第二类），唯一出口是人工 `finish --abandon` 或 reopen——删远端分支既不改变该
状态，也不构成替他人收口。

## PR 与 Merge Queue

- `main` 启用分支保护，PR 是唯一合入路径；不要直推或手动点击 Merge；
- `.github/workflows/enable-auto-merge.yml` 维护 **FIFO auto-merge 队列**
  （`scripts/ci/pr-automerge-queue.sh`）：eligible = 同仓库、非 draft 的 open PR；
  **队首 = eligible 中创建时间最早者**（脚本按 `createdAt` 升序取），且**只有队首**启用
  auto-merge（其余 `--disable-auto`，多持有者会被遥测判为破坏不变式）；
- **次序语义**：队首合入后下一个依次顶上；**新开的 PR 排到队尾、不插队**（不是最新优先）。
  排队等待时间 ≈ 「创建时间早于本 PR 的 open PR 数」× 单个合入间隔（2026-09-20 实测稳态
  **2.5–3 分钟/个**；红队首停摆时整队暂停，见下节）；
- **禁止 Execution 自持 auto-merge**：PR 跑到「就绪 + Registry 登记」为止，合入交给
  队列——不得自行 `gh pr merge --auto` / GraphQL `enablePullRequestAutoMerge`
  （含 `--squash`），也不得替其他 PR 做 update-branch / nudge。多持有者会破坏 FIFO
  串行集成；仓库已关闭 squash 合并（`allow_squash_merge=false`），squash 合入还会让
  Registry 的 merge 主题通道静默失效；
- `.github/workflows/pr-update-branch.yml` 与队列 reconcile 在队首通过 required checks
  且落后 `main` 时更新分支；
- **判读 PR 终态用 `gh pr view <N> --json state`**（`OPEN` / `MERGED` / `CLOSED`）；REST
  `GET /pulls/<N>` 的 `.state` **只有 `open` / `closed` 两个取值——已合入的 PR 也返回
  `closed`**，合入事实在独立字段 `.merged` / `.merged_at`。轮询监控合入、或判「关闭未合」
  时按 REST `.state` 会误判，REST 侧必须写 `state==closed and merged==false`；
- fork、`frontend-major` 和 `github_actions` 更新不进入自动合入；
- required checks：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`；
- PR-Agent advisory review 已下线（2026-09-16）：无自动 LLM 审查、无 `/review`
  命令通道；安全面靠 CodeQL + 确定性门禁。历史决策见
  [`2026-09-16-retire-pr-agent-advisory.md`](../notes/process/2026-09-16-retire-pr-agent-advisory.md)。

Auto-merge 的队列与分支更新以 workflow 和
[`scripts/ci/pr-automerge-queue.sh`](../../scripts/ci/pr-automerge-queue.sh) 为事实源。

### 队首停摆的判读与处置（#1246）

队首 required check 未通过时，队列**不再只是日志一行**：reconcile 会开/更新一个
`ci/queue-blocked` 去重 issue（同型 `ci/backstop-failed`；指纹未变不刷屏，队首恢复
或队列清空自动关闭）。告警只承载可见性，不参与合入决策；issue 操作失败不影响队列
行为。

收到告警（或怀疑停摆）时：

1. **判读**：`python -m tools.dev.queue_head_telemetry`——只读输出队首、阻塞原因
   （required check 未过 / 落后 main / 冲突）、已卡时长与失败日志；
2. **处置（人工，择一）**：修复该 check（owner）/ 解冲突 / 让位（把该 PR 关闭或改
   draft）；随后 reconcile（每小时 cron 或任意 PR 事件）自动推进并关闭告警；
3. `main` 合入纪律不因停摆豁免——**不要手动 Merge**。

队首 required check「没上报」有两种相反成因，reconcile 会自行分辨（判据是该 head sha
上 `ci.yml` 的 run 数，`scripts/ci/pr-automerge-queue.sh`）后分别处置（#2556）：

- **从未创建**（run 数 0 ＝ GitHub 侧触发丢失，不可预防、也没有可修的 check）：执行
  **一次** base-change push（复用队列自身的 update-branch 路径）以重新触发 CI；同一
  `(队首 PR, head_sha)` 至多一次——冷却标记写在 `ci/queue-blocked` 告警正文里，同一
  sha 第二次仍无 run 就停手并改为「需人工」。自愈后换了 head sha 即是一次全新判定；
- **跑了没上报**（run 数 > 0）：说明触发正常而该 workflow 没上报，属人工排查
  （workflow 被禁用/改名/卡审批）。

> 红队首（`FAILURE`/`CANCELLED` 等**有结论**的失败）的「自动重基逃生」仍未启用——
> 重基一个真红的队首只会反复烧队列；如再现「陈旧红」（check 结果早于 main 推进、
> 重基后可绿）实证，按收紧谓词另行立项。

## 关单关键词与自动关单

closing keyword（`Closes #N` / `Fixes #N`）由 GitHub 服务端在**合入**时解析并
关闭关联 issue——但仅当**合入执行者是人类身份**。

**根因（2026-09-08 对账 + 沙盒实验钉死，#1101）**：mergedBy=github-actions
（GITHUB_TOKEN）的合入不触发 linked-issue 原生关闭，且其产生的
`pull_request: closed` 事件被级联抑制（不触发新 workflow run）——与
「GITHUB_TOKEN 事件不产生新 run」同族。mergedBy=DUElost（人类 token）的合入
4/4 秒级原生关闭。关键词写法（全角括号、句号、空格）均非失效因子；2026-09-07
「平台故障窗口」假说已证伪（当日 TLS/GraphQL 异常与关单失效仅为时间重合）。

**机制**：auto-merge 由谁最后启用，GitHub 就以谁的身份执行合入。队列
reconcile（enable-auto-merge.yml / pr-update-branch.yml）以 `AUTO_MERGE_PAT`
（人类身份 fine-grained PAT，仅限本仓库 Contents/Pull requests RW；未配置时
回退 GITHUB_TOKEN）启用队首 auto-merge，因此队列合入落在 DUElost 身份上，
原生关单与事件级联均正常。

**纪律**（不因机制修复而免除）：

- 合入后**核销 issue 实际关闭**（查 issue state，勿以 PR body 为准），未关即
  手工补关（附证据评论）；
- `main-ci-backstop.yml` 每日 PASS 后以 `closingIssuesReferences` 兜底补关；
  其「main 前进即整体跳过」guard 意味着密集合入日可能整天不补——兜底不可
  依赖，手工核销仍是第一道；
- 排查顺序：合入执行者身份（`mergedBy`）→ `closingIssuesReferences` 是否
  建立 → 关键词写法。

## GitHub 交互的幂等与重试（#2131）

本机经代理访问 GitHub API 偶发 EOF/502（`gh` 与 `git fetch` 都会撞上）。重试是必须的，
但**「失败就重发」与「比较评论条数」都会产出重复评论**：2026-09-15 #735 的一次记录连发
4 条——`before` 取数失败为空 → `[ after -gt before ]` 恒假 → 每轮都发。

写 issue/PR 评论一律用 `tools/dev/gh_comment_once.py`：

```bash
python tools/dev/gh_comment_once.py --issue 735 --body-file /tmp/note.md
python tools/dev/gh_comment_once.py --pr 2097 --body-file note.md --marker 2097-review --update
cat note.md | python tools/dev/gh_comment_once.py --issue 735 --body - --json
```

- 幂等键 = 正文尾部的隐藏 marker（`--marker` 可显式钉住，缺省按正文派生）；
- 发布前查重：命中 → 跳过（`--update` 则 PATCH 原评论，不新增）；
- 创建失败后**先复读**：已落地（响应丢失）→ 视为成功；确认不存在 → 才允许重试；
- 查重不可用 → **不发**，退出码 2（可安全重跑）——「未知」不等于「未发布」；
- `--force` 是危险出口（跳过查重），仅人工确认未发布时使用。

同一纪律适用于其它非幂等写动作（发评论、建 issue、推标签）：**判定落地靠内容/ID，
不靠计数**；取数失败先按「未知」处理，宁可重跑也不要重复副作用。

## CI 分层

PR 合入路径只运行轻量 required checks。完整 backend tests、frontend tests/build 和
Docker build 由手工 full workflow 或 `main-ci-backstop.yml` 夜间兜底，避免把长任务
放进约两分钟的同步注意力窗口。

### 夜间红灯的前移触发规则（#1525 决策）

夜间 `main-ci-backstop` 红灯 = 缺陷已合入 main、最迟次日暴露（敞口 ≤24h）。敞口不
引入 Merge Queue 处理（成本 ≈10× 全量 CI/天；数据不支持），按**类**前移：

- **同类夜间红灯 ≥2 次 → 评估将该类前移为 PR 侧检查**（required 或信息性）；单次
  偶发不动作；
  - ✅ **输入已补（#2333，2026-09-16）**：兜底单现在自带「重跑结论 + 缺陷/flake 分类」——
    红灯时对失败 job **自动 rerun 一次**：**转绿 = flake**（走"去 flake"，**不进入**前移评估），
    **仍红 = 确定性缺陷**（才进入前移评估）。对 flake 前移会把 flakiness 引进合入路径，与
    两分钟注意力窗口的取舍相反。重跑仍红时兜底单附失败用例名（机械 grep，取不到则显式写原因）。
    另：`backend-test` 类红灯已占 `#1525` 量化样本的 5/6（近 30 次 backstop 9 红夜）。
- 前移先例：迁移空库类 → `pr-migrate-empty-db`（required）；agent 类 →
  `pr-agent-tests`；repo-level 根测试类 → `pr-agent-tests` 的 `Run repo-level
  tests` 步骤（#1569，已落地：`tests/` 离线子集，容器类两文件在 job 内 `--ignore`）。

当前分层：

- **PR 侧 required**：`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、
  `pr-agent-tests`、`pr-migrate-empty-db`；
- **PR 排除**（main push / 手工 dispatch / 夜间兜底跑）：`backend-test`、
  `frontend-check`、`docker-build`；
- 决策量化依据（近 30 次 backstop：9 红夜 ≈ 每 3 天/每 33 PR 一次；`backend-test`
  类占 5/6、前端/docker 零次）：见
  [`2026-09-12-pr-gate-promotion-rule-1525.md`](../notes/process/2026-09-12-pr-gate-promotion-rule-1525.md)。

Dependabot 的 frontend patch/minor 可自动合入；frontend major、TypeScript major 和
GitHub Actions 生态更新需要人工评审。全量 CI 失败由 backstop 使用
`ci/backstop-failed` issue 去重通知，恢复后自动关闭。

相关取舍：

- [`2026-08-14-merge-path-attention-budget.md`](../notes/process/2026-08-14-merge-path-attention-budget.md)
- [`2026-08-29-serial-automerge-update-branch.md`](../notes/process/2026-08-29-serial-automerge-update-branch.md)
- [`2026-08-30-pr-agent-fully-async.md`](../notes/process/2026-08-30-pr-agent-fully-async.md)（已由下线 note 取代现行语义）
- [`2026-09-16-retire-pr-agent-advisory.md`](../notes/process/2026-09-16-retire-pr-agent-advisory.md)
- [`2026-09-12-pr-gate-promotion-rule-1525.md`](../notes/process/2026-09-12-pr-gate-promotion-rule-1525.md)

## 批次交付流程（ADR-0058）

多个 issue 属同一缺陷形态、需要一次协调激活、或涉及数据丢失 / 安全 / 难回退语义时，走批次：
Owner 批准批次与 Appetite → 规划者出方案（批次 issue 正文）→ 各实施单元按普通实施领单 →
独立复核 → 激活 → 分层验收。职责与闸门以 ADR-0058 为准；做法与模板见
[`batch-planning.md`](ai/batch-planning.md)、[`batch-review.md`](ai/batch-review.md)。

- **待复核单元保持 draft**：FIFO 队列跳过 draft。复核者在 PR 下评论「通过」后由 Owner 转 ready；
  实施者修完 CI 也保持 draft。复核前合入是可恢复的流程违规，复核改到激活前补做；
- **激活闸门**：部署、`--publish`、scan、重指 plan_step 之前，所有「必须复核」单元已通过；
  重指单独请 Owner 确认；
- **集成观察者只报告**：「队列里没有 ready 的 PR」是集成状态，不是复核状态，不构成把任何
  draft 转为 ready 的理由；观察者无 ready、合入、方案或复核结论的决定权；
- **关单**：需要激活才生效的改动用 `Refs`，激活并贴出生效证据后再关 issue。

现行承载（角色语义由 ADR-0058 定义；具体 Web 工具由 Owner 选择，不作为契约固定项）：

| 职责 | 现行承载 | 进入 Registry |
|---|---|---|
| 规划者 | Owner 每批选择的独立 Web 工作面（如 ChatGPT Web / Claude Code Web） | 否 |
| 实施者 | 开发者选择的本地 / 云端 Harness（M2） | 仅协调域内 |
| 复核者 | Owner 按复核对象选择的独立 Web 工作面；高风险 / 治理实验优先与被复核面使用不同 Web/Harness | 否 |
| 集成观察者 | Grok Bot | 否 |
| Owner | 开发者本人 | 否 |

云端实施者形成首个可提交的有效改动后立即开 draft PR，不制造空提交或伪实现；不代登记
不存在的 worktree，不新增 Registry 字段或远程 Registry，不因实施资格获得生产写授权。
复核、ready 与激活闸门对本地 / 云端实施相同；职责不按代码类型划界。

Planner / Reviewer 可以位于 STP Project 中，但 Project 内历史聊天属于**共享背景**，不是事实源或裁决源：
每个新会话都必须从当前 `main`、当前 issue / PR、Accepted ADR 与现行治理文档重新取证；不得把旧会话中的
修法、判断或模型结论直接当作本批既定答案。普通复核允许「同 Project + 独立新会话 + 独立取证」；
安全、数据丢失、难回退、架构 / 治理实验和 Planner 质量验证优先使用不同 Web/Harness。该选择不进入 Registry，
也不新增标签或门禁；仅在批次实验或复盘需要统计时，在批次 issue / 复核记录中留实际工作面事实。

## 冲刺期的结构护栏

临近节点时按 ADR-0034 多 Harness 并行消解积压，合入量会成倍上升；不为此设合入上限
（ADR-0034 §2.6 v1.9 撤销会话上限的同一理由：会被常态突破的上限只是文档漂移）。
CI 兜底与每日审计原本只覆盖**行为**（修复是否落地），以下四处补上**结构**：

- **CI 结构门禁**：`lint` job 的「分层检查」跑仓库根 `.importlinter`（C1–C5），分层退化
  由 CI 拦下，不依赖人读 diff；已知违规是只减不增的基线；
- **领单前分流**：按 [ADR-0058](../adr/ADR-0058-planned-batch-execution.md) D7 分流——满足强判据的
  走批次（见下节），其余单点问题直接领单实施；`needs-decision` 是辅助信号，由开发者在 issue 里
  写一行选定方案，或交规划者判断是否升级为批次。当前是标签约定，若要在 `ai_work declare`
  中机械拦截，属于改变执行语义，须先修订 ADR-0034；
- **结构日报**：每日审计时运行 `python tools/dev/structure_digest.py`（默认近 1 天合入、
  7 天热点窗口），一页看合入构成、合约基线变化、三次法则热点（7 天内被 ≥3 个 fix PR
  改过的生产文件）、新增门禁、过渡登记与兜底标记的变化；
- **冲刺后结算**：节点过后以结构日报的热点清单与基线变化为输入，给热点开根治单，在下一次
  冲刺前消化。触发依据是实测数据（与 ADR-0034 §6 同口径），不是会话数或 PR 数。

## 文档维护

- 常驻文档写当前规则，不记录编年变迁；
- 变更历史进入 commit、PR、ADR 或 Agent Note；
- 代码和测试与文档不一致时，以代码和测试为准并回写权威文档；
- `docs/archive/` 只保存历史材料，不新增现行规范。

## 不变量违规处置（棘轮，#855 收口）

发现硬不变量/契约违规（review、CI、生产审计均可触发）时按序处置：

1. 查[强制力覆盖图](../design/2026-08-governance-surface-protection.md)：
   该不变量当前靠什么强制（运行时 / gate / 结构自证 / residual）；
2. 文本缺失（AGENTS.md 或锚点没有）→ 补文档；文本在场且**可机器查** →
   给差异面检查加策展模式；**不可机器查** → 在覆盖图 residual 列登记
   （review 兜底）；
3. 「没写到 vs 写了没传导」的归因只在选择修复端时需要：文本缺失=前者；
   在场但违规=后者，修复走第 2 步的机器查/residual，**不重建行为验证层**
   （2026-08-26 挂载裁决 + 2026-09-07 #855 收口）。

## Git 破坏性操作纪律（#930）

共享工作树 + 多会话并行环境下，破坏性 git 命令的爆炸半径是**他人进行中的
工作**，不只自己的：

| 操作 | 风险 | 处置 |
|---|---|---|
| `git reset --hard` | **不可逆**销毁未提交工作（含并行会话的） | **禁止**；未提交工作显式 commit/分支保存 |
| `git stash drop` / `clear` | **不可逆**销毁已暂存现场 | **禁止** |
| `git stash`（创建） | 可恢复，但污染全局 `refs/stash` 栈——多会话 pop/apply 错位会拿错别人的现场 | **禁止**，走显式分支 |
| `git stash list/show/pop/apply/branch` | 读侧/恢复侧 | 放行 |

**强制层**：

1. Claude 会话：PreToolUse hook（`.claude/settings.json` →
   `tools/dev/check_destructive_git.py`）按引号/转义与简单命令语义解析，递归检查
   静态 shell 包装器、命令替换与可执行 heredoc；命中上表禁止项即 exit 2。
   解析失败走保守回退，疑似命中仍 exit 2；检查器异常/脚本缺失 exit 1，
   可见告警、不阻断。已知动态缺口与边界以脚本 docstring 为准；
   `--self-test` 与手动 `destructive_git_probe.py` 验证脚本，不替代真实会话触发验收；
2. git 级观测：`.githooks/reference-transaction` 对 `refs/stash` 更新留痕
   告警（opt-in：`git config --local core.hooksPath .githooks`）——`reset --hard`
   的 worktree 破坏没有 git 级拦截点（ref 事务无法与普通提交区分），该命令
   依赖第 1 层 + 纪律；
3. 棘轮：同族命令（`git restore .` / `git clean -f` / `git checkout -- .`）
   暂未入拦截清单，出现事故按不变量违规处置流程扩展。

### Git hooks 的 opt-in 状态与自检

`.githooks/pre-commit` 与 `reference-transaction` 是可选的本地辅助；文件存在
不代表已启用，也不替代 PR/CI 或 Claude PreToolUse。Git 按当前配置解析 hooks
目录，并忽略没有执行位的 hook（[Git 官方说明](https://git-scm.com/docs/githooks)）。
本轮不默认修改宿主配置；是否启用由开发者选择。以下从仓库根运行，项目解释器
先按 local-development 准备；脚本定位后，内部支持深层 cwd：

```bash
.venv/bin/python tools/dev/check_git_hooks.py # 只读状态
git config --local core.hooksPath .githooks  # 选择 opt-in 后显式执行（同克隆共享）
.venv/bin/python tools/dev/check_git_hooks.py --self-test
```

状态 `DISABLED` 表示本仓库 hooks 未启用，退出 0 只是只读查询成功；`CONFIGURED`
只证明实际路径和执行位可用。若实际使用本 worktree 之外的可执行 hooks，仅报告路径与
`UNVERIFIED`，不读取/执行它们或断言未启用。显式自检要求已配置，否则为 `UNVERIFIED` / 非零。
自检只在临时隔离仓库复制当前 hook，从真实 Git 验证污染提交被阻断、`refs/stash`
事务告警且不阻断；不改调用者的 config/index/refs，不执行 `git stash` 或 reset。
`PASS / FAIL / UNVERIFIED` 分别表示行为匹配、已复现不匹配、无法验证；仅 PASS
可作自检通过证据。linked worktree 各自解析其 `.githooks` 文件，须分别检查。
使用当前项目解释器，入口准备见 local-development；Python 稳定导航由 #3516 G3 收口。
