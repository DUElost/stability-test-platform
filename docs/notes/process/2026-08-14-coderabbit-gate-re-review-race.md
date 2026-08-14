# CodeRabbit gate 复评竞态：#267 复评落定晚于合入

Status: implemented
Class: process

## Decision

`enable-auto-merge.yml` merge-gate 的 skipped/paused 分支增加前置守卫：当本
PR 历史上有**未撤销**的 CodeRabbit `CHANGES_REQUESTED`（任意 commit）时，
新 head 上 skipped/paused 不再写 success，改为 pending——必须等 CR 对当前
head 给出终态（review 事件写 gate）或明确 rate limited 才放行。

同轮追加的防御（#268 CR 意见，两轮）：

1. **status 事件重算路径**：CR 的 commit status 变化（rate limited 落地等）
   不伴随 review 事件；workflow 增加 `status` 触发，job 级 if 只放行
   `CodeRabbit` context，按 commit 反查 open PR 后走非评审路径重算。否则
   gate 写 pending 后若只有 CR 的 status 变化，pending 会永远停留。
2. **查询失败写 pending**：历史评审查询（`prior_cr`）失败时写 pending
   并正常退出——`set -e` 下静默终止会残留旧 success 放行。
3. **统一并发键 + 全量最终守卫**：concurrency group 统一按 HEAD sha
   （PR 事件取 head.sha，status 事件取 commit sha），同一 head 的所有事件
   共享一个组，避免 PR 事件 run 与 status 事件 run 分组不同而竞写状态；
   所有**非评审** success 写入（dependabot / rate limited / skipped+paused /
   落定 APPROVED）统一经 `guard_before_success()`：写 success 前重查当前
   head 终态决策（新落入的 CHANGES_REQUESTED → failure）与最新 workflow
   run（被取代 → 退出不写），查询失败写 pending。

背景（#267 实测时序）：push 修复 + `@coderabbitai review` 后，CR 手动复评
还在排队，其 commit status 短暂显示 skipped；gate 按「skipped→放行」写
success，六个必查项全绿后 auto-merge 合入（01:47:20Z），CR 复评终态
（CHANGES_REQUESTED，3 条 Minor）5 分钟后才落地（01:52:08Z）——此时 PR 已
非 open，review 事件触发的 merge-gate job 被跳过，不再写状态。

逃生阀：dismiss 旧 review（state 不再是 CHANGES_REQUESTED）后，任意
pull_request 事件重跑会**重新评估**而非无条件放行：当前 head 仍有
CHANGES_REQUESTED → failure；无终态 → pending；仅历史与当前 head 都干净
且状态 skipped/paused 时才 success。CR 的 status 变化经 status 事件同样
重算。

## Alternatives

- 等复评期间完全不看 skipped、一律 pending：拒绝。没有 CHANGES_REQUESTED
  历史的新 PR 会因 CR 配额跳过被永久卡住。
- 改为等待 review 事件驱动（不轮询 commit status）：拒绝。CR 不可用时
  review 事件不来，同样卡死；轮询 + 终态判断是现有框架。
- 保持现状接受竞态：拒绝。用户明确要求「CR 有问题则 Change Request 阻塞
  合入」，竞态直接掏空该语义。

## Verification

- `enable-auto-merge.yml` YAML 语法校验通过；
- 行为验证依赖真实场景：对有过 CHANGES_REQUESTED 的 PR push 新 commit，
  观察 gate 在复评落定前保持 pending、复评 APPROVED/CHANGES_REQUESTED 后
  由 review 事件写终态；status 路径（CR 仅发 commit status 的场景）待
  下一个 rate limited 实例验证。

## Revisit

若 CodeRabbit 配额政策变化导致「复评永不落定」常态化，重新评估逃生阀的
便利性（如 allowlist 指定跳过复评的 label）。
