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

## 补充（2026-08-30，并发 reconcile 的 head-sha 竞态）

两个 workflow 都跑 `pr-automerge-queue.sh`，但 concurrency group 不同
（`pr-automerge-queue` vs `pr-update-branch-<head_branch>`），可对**同一队首**
并发调 `updatePullRequestBranch`。该 mutation 带 `expectedHeadOid`，胜者改掉
head 后，败者报 `head sha didn't match the current head ref`；脚本 `set -e`
遂把「别人已经做完了」刷成红 X，并连带跳过同一 job 的第二个 step。实例：
run 33269782605（19:01:33 失败，同秒的 33269780623 已成功更新 #571，#571 于
19:05 正常合入）。不属 required checks，不阻塞合入。

两处改动：

1. `pr-update-branch.yml` 的 concurrency group 统一为 `pr-automerge-queue`
   （repo 级 group 跨 workflow 生效），消除并发源。代价：本 workflow 第二个
   step 一并串行，单 job ~10s，可接受。
2. 两个脚本各内联 `update_branch_tolerant`：仅对
   `didn't match the current head ref` 归零并打印说明，其余失败原样返回非零。
   纵深防御——`gh pr update-branch` 读 head 与 mutation 之间仍有窗口，且
   schedule / 手动 dispatch 未必都落在同一排队路径上。

放弃的备选：

- **只加容错**：竞态照旧发生，只是不显红；诊断信息反而变少。
- **只统一 group**：留不住 mutation 窗口内 head 被改的情形（如人工 push）。
- **失败重试**：先重读状态再重试会把 10s 的 job 拉长、逻辑绕，与合入路径
  注意力预算冲突。
- **拆成两个 job、各用各的 group**：多一次 checkout，且仍需
  `continue-on-error` 才能不让 reconcile 挡住第二个 job，更绕。

验证：`bash -n` 两脚本 + `yaml.safe_load` 工作流；桩 `gh` 三路径断言
（race→0、其他错误→非零、成功→0），两个脚本各跑一遍。

## Revisit

若 open PR 常态 >3 或出现外部 fork 贡献潮，再评估轻量队列状态 API 或
required check 列表变更时的脚本同步方式。

- GitHub 若改写该 GraphQL 报错文案（如改用 `expectedHeadOid` 措辞），需同步
  `update_branch_tolerant` 的匹配串，否则会退化回红 X。
- `enable-auto-merge.yml` 的 `cron: */10` 兜底自 2026-08-29 17:39 UTC 加入后，
  截至 19:12 UTC 零次 schedule 运行（近 12h 的 100 次运行全为 `pull_request`，
  工作流 `state=active`）。当前队列靠 PR push 推进未停滞，但若该兜底长期不
  触发，需另找 merge 后的 reconcile 触发点。
