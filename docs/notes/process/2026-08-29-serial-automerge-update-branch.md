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
   auto-merge、6 项 required checks 均为 SUCCESS、且 `behind_by > 0`，则
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
- `enable-auto-merge` 脚本 checkout 不得 pin `ref: main`（首合入 bootstrap）。
- required 列表与分支保护同步（2026-08-29 起 6 项，不含 pr-agent-gate 顾问）。

## 补充（2026-08-30，merge 后 reconcile 兜底）

`GITHUB_TOKEN` auto-merge 合入常抑制 `pull_request closed` workflow，队首合入后
下一 PR 可能长时间无 auto-merge（#557 实测）。补丁：

- `enable-auto-merge.yml` 增 **每 10 分钟** `schedule` reconcile（无 open PR 时
  脚本空转退出）；
- `pr-update-branch.yml` 在每次 update 判断后 **顺带 reconcile**（任 PR CI 完成
  时修正队列）。

## 补充（2026-08-30，reconcile 后队首主动 update）

队首换档后下一 PR 常无新 CI，`workflow_run` 链断。`pr-automerge-queue.sh` 在
FIFO reconcile 末尾增加：队首已挂 auto-merge、6 项 required 全 SUCCESS、
`behind_by > 0` → `gh pr update-branch`（不依赖 `workflow_run.head_sha`）。
schedule reconcile（≤10 分钟）与任 PR CI 完成后的 reconcile 均覆盖此路径。

## Revisit

若 open PR 常态 >3 或出现外部 fork 贡献潮，再评估轻量队列状态 API 或
required check 列表变更时的脚本同步方式。
