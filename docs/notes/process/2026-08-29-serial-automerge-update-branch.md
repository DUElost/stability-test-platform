# 串行 auto-merge + 落后 main 自动 update branch

Status: implemented
Class: process

## Decision

多 PR 并行挂 auto-merge 时，任一先合入会使其余 PR 变 `BEHIND` 并触发整轮
CI 重跑。采纳两项低成本缓解（否决 Merge Queue，见
`2026-08-14-merge-path-attention-budget.md`）：

1. **FIFO 串行 auto-merge**：`enable-auto-merge.yml` 改为 reconcile 队列——仅
   最老 eligible open PR 保留 `--auto --merge`，其余 `--disable-auto`。
2. **全绿且落后时自动 update branch**：新 workflow `pr-update-branch.yml` 在
   CI / CodeQL / PR Agent 任一 `workflow_run` 成功结束后，若 PR 已挂
   auto-merge、7 项 required checks 均为 SUCCESS、且 `behind_by > 0`，则
   `gh pr update-branch` 一次（`workflow_run.head_sha` 须与 PR head 一致，
   避免过期 run 误触发）。

脚本：`scripts/ci/pr-automerge-queue.sh`、`scripts/ci/pr-update-branch-if-behind.sh`。

## Alternatives

- **Merge Queue**：每个 PR 等一轮全量再合入，与合入路径注意力预算冲突。
- **仅人工「同时少挂 auto-merge」**：零代码但易忘；自动化队列保留习惯约束。

## Verification

- 开两个同仓库 PR：仅较早创建的挂 auto-merge；合并队首后队尾自动启用。
- 队首全绿且 `BEHIND`：观察 `PR update branch if behind` workflow 调用
  update-branch 并触发一轮新 CI。
- `pr-agent-gate` failure 仍由 #421 步骤 `--disable-auto`；reconcile 不会
  给非队首重新挂上。

## Revisit

若 open PR 常态 >3 或出现外部 fork 贡献潮，再评估轻量队列状态 API 或
required check 列表变更时的脚本同步方式。
