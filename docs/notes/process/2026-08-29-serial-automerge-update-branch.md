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

## 补充（2026-08-30，队首 workflow run 自动批准）

PR 分支含 `.github/workflows/` 变更（含 merge main 带入）时，GitHub 将
`pull_request` 触发的 run 置为 `action_required`，须维护者点 Approve（#570
实测：CI / Enable auto-merge / PR Agent 三条同时挂起）。`pr-automerge-queue.sh`
在 FIFO reconcile 开头对**队首分支**查询 `status=action_required` 并调用
`POST .../actions/runs/{id}/approve`；`enable-auto-merge.yml` 与
`pr-update-branch.yml` 增 `actions: write`。批准失败只打日志、不阻断 reconcile。
仅队首、且队列已排除 fork / dependabot major。

放弃的备选：全 open PR 批量批准（扩大攻击面）；专用 PAT（运维负担）。

## 补充（2026-08-30，合入与 update-branch 的竞态）

`update_branch_tolerant` 最初只吞 `head sha didn't match`（并发 reconcile 互抢）。
run 33306014644 暴露第三类：脚本查状态时 PR #583 仍 open（stale 检查也过），
随即被 auto-merge 合掉，`update-branch` 报 `Cannot update PR branch due to
conflicts` —— PR 已合入，这次更新本就无意义。

改为**失败后重查 PR 状态**：`MERGED` / `CLOSED` → 打印说明并返回 0，其余失败
原样返回非零。按状态判定而不是再堆一条报错文案匹配——合入与 update-branch 的
竞态不只有一种报错形态，而「PR 已经不在 open 态」是唯一稳定的判据。

放弃的备选：给冲突文案再加一条 `grep`（文案随 gh / GitHub 版本变，会持续漏）；
update 前后各查一次状态做乐观锁（缩短窗口但不消除，还多两次 API 调用）。

验证：桩 `gh` 四路径断言（head-sha 竞争→0、其他错误 + OPEN→非零、其他错误 +
MERGED / CLOSED→0、成功→0），两个脚本各跑一遍。

## 补充（2026-08-30，队首合入后的 reconcile 触发点）

队首合入后必须立刻给下一 PR 挂 auto-merge，但那一刻没有任何事件可用：

- `pull_request closed`：auto-merge 合入常被级联限制抑制（#557 实测）；
- `workflow_run`：main 上唯一的 workflow 是 **CodeQL 默认 setup**（仓库无
  `codeql.yml`），默认 setup 的 run **不参与 `workflow_run` 链**——实测 #587
  于 11:23:59Z 合入后，没有任何新的 `pr-update-branch` run；
- `cron: */10`：**实际被 GitHub 节流到 2–6 小时一次**。2026-08-30 统计：
  25.6h 内只有 4 次 schedule run（21:20 / 23:33 / 01:39 / 07:28），全部
  success；同期 96 次为 `pull_request`。对照 `main-ci-backstop.yml` 的每日
  cron 正常触发，说明仓库级 schedule 是通的，问题在高频调度本身。

故改用 `on: push: branches: [main]`：main 每次前进（即每次合入）都触发一次
reconcile（~10s job，concurrency group 仍串行）。cron 保留作低频兜底。

放弃的备选：放宽 `pr-update-branch.yml` 的 `workflow_run` 到 `event == 'push'`
（默认 setup 的 run 根本不触发 workflow_run，无从放宽）；把 reconcile 塞进
`main-ci-backstop.yml`（每天一次太稀疏，且混进与队列无关的职责）。

验证：合入后观察下一次 main 前进——应出现 event=push 的 `Enable auto-merge`
run，并在队首全绿且 BEHIND 时执行 update-branch。

## Revisit

若 open PR 常态 >3 或出现外部 fork 贡献潮，再评估轻量队列状态 API 或
required check 列表变更时的脚本同步方式。

- GitHub 若改写该 GraphQL 报错文案（如改用 `expectedHeadOid` 措辞），需同步
  `update_branch_tolerant` 的匹配串，否则会退化回红 X。
- 若 main 的 push 事件将来也被抑制（GitHub 策略变更），退回 cron 兜底前先确认
  `main-ci-backstop.yml` 的每日 cron 是否仍正常——它是判断「仓库级 schedule
  是否还活着」的对照样本。
