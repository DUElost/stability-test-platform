# reconcile 队首 mergeMethod 预检的瞬时故障容错

Status: implemented
Class: process

## Decision

`scripts/ci/pr-automerge-queue.sh` 的队首 `mergeMethod` GraphQL 预检改为
`head_merge_method()`：重试 3 次（间隔 5s），仍败则放弃预检、直接执行幂等的
`gh pr merge --auto --merge`，不再让瞬时故障经 `set -e` 把整个 reconcile
run 刷成红 X。

根因（2026-09-01 run 33508879071，PR #725 事件）：日志尾部
`GraphQL: Something went wrong while executing your query ... E05B:268019`
——GitHub 端瞬时 5xx，不是队列逻辑问题；该 run 的 PR 四分钟后照常合入。
预检自 c5d8d21e 起只为日志区分「已启用/新启用」，`enablePullRequestAutoMerge`
对已启用的 PR 重复调用同样成功，回退无条件 enable 无行为差异。enable 调用
本身**不**加容错：真失败（权限/冲突/不可合并态）就该红，那是唯一有效信号。

## Alternatives

- **整个脚本对 gh 失败一律 tolerant**：放弃。reconcile 是收敛循环（每小时
  cron + 每个 PR 事件重跑），但把 enable/update 的真实失败也吞掉，权限被撤
  之类的问题将只剩「日志里少了 Enabled 行」这种隐性信号。
- **只匹配 "Something went wrong" 文案再容错**：放弃。文案不稳定（对照
  `update_branch_tolerant` 注释里的两种已知文案），按「重试后仍败的查询降级」
  处理对任何瞬时形态都成立，不依赖文案。
- **不修，接受偶发红 X**：红 X 出现在 main 的 Actions 列表里会被误读为
  CI 故障进入分诊（09-03 审计即被列入），噪声本身有成本。

## Verification

- `bash -n` 通过；mock `gh` 单测三种情形：2 次瞬时失败后成功（rc=0 取到值）、
  持续失败 3 次（rc=1 + stderr 降级提示）、立即成功（rc=0 无重试）。
- 合入后由 enable-auto-merge.yml 自证的回路验证：pull_request 事件跑的正是
  本 PR merge commit 里的脚本版本，后续 PR 的 reconcile run 全绿即证明。

## Revisit

若未来 GitHub GraphQL 瞬时故障变密集、3 次重试不够，可考虑指数退避或把
预检彻底删掉（每次直接幂等 enable，代价只是日志不再区分两种文案）。
