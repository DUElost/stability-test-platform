# 队首 PR 自动批准 action_required workflow runs

Status: implemented
Class: process

## Decision

GitHub 在 PR 含 `.github/workflows/` 变更时不自动跑 `pull_request` workflow，
直至维护者批准（`action_required`）。#568 合入后队首 sync main 常批量触发
（#570：CI / Enable auto-merge / PR Agent 同时挂起），FIFO 队列因此停滞。

在 `pr-automerge-queue.sh` reconcile **开头**对队首 `headRefName` 查询
`GET /actions/runs?status=action_required&head_branch=...`，逐条
`POST .../runs/{id}/approve`。两 workflow job 增 `permissions.actions: write`。

## Alternatives

- **人工点 Approve**：可靠但每 PR 重复，与合入路径自动化目标冲突。
- **批准所有 open PR**：扩大面；恶意 workflow 注入面更大。
- **专用 bot PAT**：多一套密钥轮换。

## Verification

- `bash -n scripts/ci/pr-automerge-queue.sh`
- 合入后队首 PR merge main → 观察 Enable auto-merge reconcile 日志含
  `Approved workflow run`；Checks 不再显示 awaiting approval。
- 本 PR 自身合入后首次 run 可能仍须一次人工批准（workflow 变更 bootstrap）。

## 补充（2026-08-30，GET 查询参数修复）

run 33306014644 reconcile 日志：`integer expression expected` /
`jq: Cannot iterate over null`。根因：`gh api -f` 用于 GET 时响应缺
`workflow_runs`。改为 URL query（`?status=...&head_branch=...`）+ `// []`
兜底 + count 非整数时按 0 处理。

## 补充（2026-08-30，pull_request_target 即时批准）

#596 实测 reconcile 批 run 有约 2–3 分钟空窗（须等另一 PR 触发
`pull_request`）。新增 `approve-workflow-runs.yml`（`pull_request_target`，
仅 `gh api` 批准、不 checkout PR 代码），在 sync/开 PR 后秒级批同分支待审 run。
`pr-automerge-queue.sh` reconcile 改为对**队列内全部** eligible 分支批准（非仅队首）。

## Revisit

若 GitHub 放宽同仓库 write 协作者的 workflow 审批策略，可删此逻辑。
