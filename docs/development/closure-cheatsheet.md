# 收口速查表（registry / worktree / 本地分支 / 远端分支）

> **一页速查，不新增规范**：各节的权威源见**节尾引用**，冲突以权威源为准。
> 适用场景：批次收窗、PR 合入核销、僵尸记录收口、worktree 与分支清理。

## 0. 收窗前四步（顺序固定）

1. `python tools/dev/ai_work.py update --all` —— 批量核销 PR 终态（**只扫最新 400 个 PR**，
   更早记录用单条 `update --id <requirement>` 兜底）；
2. `python tools/dev/ai_work.py drift --strict` —— 有提示先处置（STALE 人工裁决 /
   declaration-drift 补或收窄 scope / overlap 改串行）；
3. `python tools/dev/ai_work.py status --risk` —— 动手前看风险面（在窗记录 + overlap）；
4. 以上核完再动本机资源（worktree / 分支）——顺序反了会把「已合入」误判成待清理。

## A. Registry 记录

风险真值表（三条任一为真 ⇒ 在窗）：

```text
risk = integration ∈ {PR_OPEN, READY}
    OR (integration = NO_PR  AND lifecycle ∈ {CODING, FINISHED})
    OR (integration = CLOSED AND lifecycle ≠ ABANDONED)      ← PR 被关 ≠ 工作停止
```

| 情形 | 判据 | 动作 |
|---|---|---|
| 已合入 | `integration=MERGED` | 自动出窗；merge 后继续新工作须**重新 declare** |
| 僵尸 ①（声明未落地） | `NO_PR` + `CODING/FINISHED` + STALE + derived 为空 | `finish --id … --abandon`（唯一合法终点） |
| 僵尸 ②（关闭未合） | `CLOSED` + 同条件 | **仍在窗且占 issue 槽位** → 二选一：abandon 出窗 / reopen·转手重新 declare |
| 悬挂引用 | 记录 `CODING` 但 worktree 目录不存在 | 核 PR：MERGED ⇒ `finish --pr <N>`；无 PR 且 zombie ⇒ `finish --abandon` |
| 关闭未合的**等价核验** | `git cherry origin/main <branch>` 的 `+` 补丁逐文件对主干核对（同一 issue 的同修 PR 是否已带等价内容进主干） | 等价 ⇒ owner 授权后 abandon + 独有补丁打 `refs/backup/<date>/<branch>`；**不等价 ⇒ 不 abandon**（reopen/转手） |
| **缓存失效（`landed`）** | 分支/`origin/<branch>` 已是 `origin/main` 祖先，或 merge 主题含 `#<pr_number>` | 该缓存对 risk 判定失效 ⇒ 按**出窗**处理，标 `stale-cache`，用 `update --id <requirement>` 核销（`MERGED` 只能由 T6 写入） |

- `finish --abandon` **有开放 PR 时只警告、留窗**至 GitHub 终态（T4）；
- 权威源：[`ai/execution-contract.md`](./ai/execution-contract.md) §3.1–§3.3
  （§3.4 是 declare 查重，与收口无关）。

## B. Worktree

| 判定 | 条件 | 动作 |
|---|---|---|
| 可删 | 分支 patch 全在 `origin/main` **且** `status --porcelain` 为空 **且** 记录 `FINISHED` | `git worktree remove`（**不加** `--force`） |
| 保留 ① | Execution 仍 `CODING` | 不动（即使 PR 已合入） |
| 保留 ② | 有任何未提交改动（含未跟踪文件） | 不动 |
| 保留 ③ | registry 无记录（多为别家 harness 的 scratch） | 不擅动，交用户裁决 |
| 保留 ④ | main worktree 里他人的未提交文件 | 绝不触碰 |

- **每次清理前重查列表**（别家会话可能刚新建）；`/tmp/stp-*` scratch 逐文件定性后再删
  （「scratch」≠ 可删——曾藏未落地草稿）。
- 权威源：[`repository-workflow.md`](./repository-workflow.md)
  §worktree 与本地分支清理 › worktree。

## C. 本地分支

| 判定 | 判据 | 动作 |
|---|---|---|
| 安全集 | `git for-each-ref --merged origin/main refs/heads` | 可删；**不能**拿 `git branch -d` 当安全网（本地 main 常落后） |
| 改写合入 | `git cherry origin/main <branch>` 的 `+` 行数 = 0 | 可删 |
| 需人工核 | `+` > 0 | 保留或核对；确认被取代后按备份 ref 纪律处理 |
| 补核 | `+`=0 但对不上祖先 | `gh pr list --state merged --head <branch>` 核 PR 已 merged |
| 备份 ref | 仅「PR MERGED 但 patch 改写」 | `git update-ref refs/backup/<date>/<branch> <sha>` 后再删 |
| 保留 | `main` / 被 worktree 占用（`git branch -vv` 行首 `+`）/ registry `CODING` | 不动 |

收尾顺带 `git fetch --prune origin`。

- 权威源：[`repository-workflow.md`](./repository-workflow.md)
  §worktree 与本地分支清理 › 本地分支。

## D. 远端分支（共享态，动作前须人工确认）

| 判定 | 判据 | 动作 |
|---|---|---|
| 基线 | 仓库 `delete_branch_on_merge=true` | 合入即自动删，无需人工 |
| 补删 ① | 合并后被推「同步 main」提交而复活 | `git cherry` `+`=0（内容已在主干）⇒ 删 |
| 补删 ② | squash/amend 等**改写合入**（patch-id 变，`+` 不为 0） | `gh pr list --state merged --head <branch>` 核 PR 已 merged + 逐文件核对主干等价 ⇒ 删；否则保留 |
| 补删 ③ | **关闭未合**的 PR 分支 | 先做 A 的「等价核验」；等价 ⇒ 删 |
| 保留 | `main` / open PR 头分支 / registry `CODING` 分支 / 未合并且未被取代的 WIP | 不动 |

**执行纪律**：先出「待删清单 + 逐条判据」交人工确认 → `git push origin --delete <branch>`
→ `git fetch --prune` 收尾。删远端**不改变** registry 窗口状态，也不构成替他人收口。

- 权威源：[`repository-workflow.md`](./repository-workflow.md) §远端分支生命周期与补删。

## 一页以外的两条纪律

1. **abandon 是人工动作**：只对「已放弃 + 判据核完」的记录执行；关闭未合的先做等价核验；
2. **共享态与自留地分开**：远端删除先确认；本地 worktree/分支按 B/C 自行处置；
   registry 记录按 A 收口——三者的判据不互相顶替。
