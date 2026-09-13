# update-branch workflow scope 容错（#1783）

Status: implemented
Class: bug-fix

## Decision

队首 PR 改动 `.github/workflows/*` 且落后 `main` 时，`reconcile-queue` 调用
`gh pr update-branch`；`AUTO_MERGE_PAT` 无 `workflow` scope 会被 GitHub 拒绝，
`update_branch_tolerant` 落到 `return "$rc"` → 整 job 红、队列 rebase 停摆。

**修复**：在 `update_branch_tolerant` 识别 `without 'workflow' scope` /
`updatePullRequestBranch` 报错，绿退并打印人工 rebase 指引（与 head-sha 竞态、
冲突、PR 已关闭三类容错同构）。不扩 PAT scope（#1783 路径 B 留给 owner 决策）。

## Alternatives

- **给 AUTO_MERGE_PAT 补 workflow scope**：根因消除但扩大 CI 定义修改面，本单不默认做；
- **跳过改过 workflow 的 PR 的 update-branch**：需额外 GraphQL/文件列表，过重。

## Verification

- `python -m pytest tests/test_automerge_queue_alerts.py::test_update_branch_workflow_scope_denial_exits_zero -q`
- `python -m pytest tests/test_automerge_queue_alerts.py -q`

## Revisit

- Owner 若批准 PAT workflow scope，可与本容错并存；容错仍防止未来 scope 回退时再次全线红。
