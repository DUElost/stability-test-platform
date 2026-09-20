# 远端分支生命周期与补删策略（固化到 repository-workflow）

Status: implemented
Class: process

## Decision

把远端分支的清理策略写进
[`docs/development/repository-workflow.md`](../../development/repository-workflow.md)
（新节「远端分支生命周期与补删」），内容四条：

1. **基线**：合入即删由仓库设置 `delete_branch_on_merge=true` 承载（实测生效，不靠人工）；
2. **补删例外**（收窗批量核）：`git fetch --prune origin` → `git branch -r --no-merged
   origin/main` → 逐个 `git cherry origin/main <branch>`：`+`=0 ⇒ 删；`+`>0 但 PR CLOSED
   且被同修 PR 取代（逐文件核对主干等价）⇒ 删；`+`>0 无取代 ⇒ 保留或打
   `refs/backup/<date>/<branch>` 后删；
3. **保留面**：`main`、open PR 头分支、registry `CODING` 分支、未合并且未被取代的 WIP；
4. **执行纪律**：远端删除是共享态动作——先出清单+判据交人工确认再执行，之后 prune 收尾；
   与本地分支清理规程判据不混用。

首次执行（2026-09-20）：远端 4 个分支中，2 个 MERGED 分支（#2566 / #2704，`git cherry`
独有补丁 0，属「合并后被同步提交复活」）与 1 个 CLOSED 判重复分支
（`fix/2706-handover-ms04-evidence-union`，其唯一补丁的候选集修复已由 #2708 落在
`tools/site_config/handover.py:73`）经人工确认后删除；远端现仅 `main`。

## Alternatives

- **只依赖 `delete_branch_on_merge`、不写规程**：弃——它覆盖不到三种例外（复活的
  同步提交 / 改写合入 / 关闭未合），实测一天内就出现 3 个漏网；且「删哪些」没有判据时
  只能靠印象，容易误删 WIP。
- **按「最后提交时间」批量删陈旧分支**：弃——时间不表达内容归属；陈旧分支里可能有
  未合的 WIP（本仓确有 `/tmp` scratch 藏未落地草稿的先例）。
- **把远端删除并入本地清理规程（memory 里的 worktree/本地分支条目）**：弃——两侧
  风险面不同（本地是自留地、远端是共享态），判据与确认门槛必须分开写；
  本地条目留在会话记忆，共享态规则进仓库文档。
- **对 CLOSED 分支一律保留**：弃——PR 被同修 PR 取代的形态会长期堆积（本次 3 个里占 1），
  但保留「逐文件核对」这一步，避免只看 PR 标题就删。

## Verification

- 判据本身即证据：3 个删除项逐条给出 `git cherry` 独有补丁数与主干等价改动位置，
  经人工确认后执行 `git push origin --delete`；
- 收尾 `git fetch --prune origin` 复核 `git ls-remote --heads origin` 仅剩 `main`；
- `python scripts/run_gates.py check:quick` → 见 PR（含 gov-surface 对 note 头的结构校验）。

## Revisit

- 若仓库改为 `delete_branch_on_merge=false` 或引入 Merge Queue 的 autodelete 语义，
  基线一条随之改写；
- 若「关闭未合」的分支开始承载需要长期可查的证据（如验收现场），把「备份 ref」
  从可选升为强制，并把备份 ref 的保留期写清。
