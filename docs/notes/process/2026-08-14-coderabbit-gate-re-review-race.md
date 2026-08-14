# CodeRabbit gate 复评竞态：#267 复评落定晚于合入

Status: implemented
Class: process

## Decision

`enable-auto-merge.yml` merge-gate 的 skipped/paused 分支增加前置守卫：当本
PR 历史上有**未撤销**的 CodeRabbit `CHANGES_REQUESTED`（任意 commit）时，
新 head 上 skipped/paused 不再写 success，改为 pending——必须等 CR 对当前
head 给出终态（review 事件写 gate）或明确 rate limited 才放行。

背景（#267 实测时序）：push 修复 + `@coderabbitai review` 后，CR 手动复评
还在排队，其 commit status 短暂显示 skipped；gate 按「skipped→放行」写
success，六个必查项全绿后 auto-merge 合入（01:47:20Z），CR 复评终态
（CHANGES_REQUESTED，3 条 Minor）5 分钟后才落地（01:52:08Z）——此时 PR 已
非 open，review 事件触发的 merge-gate job 被跳过，不再写状态。

逃生阀：dismiss 旧 review（state 不再是 CHANGES_REQUESTED）后任意
pull_request 事件（如改个 label）重跑非评审路径即放行。

## Alternatives

- 等复评期间完全不看 skipped、一律 pending：拒绝。没有 CHANGES_REQUESTED
  历史的新 PR 会因 CR 配额跳过被永久卡住。
- 改为等待 review 事件驱动（不轮询 commit status）：拒绝。CR 不可用时
  review 事件不来，同样卡死；轮询 + 终态判断是现有框架。
- 保持现状接受竞态：拒绝。用户明确要求「CR 有问题则 Change Request 阻塞
  合入」，竞态直接掏空该语义。

## Verification

- `enable-auto-merge.yml` YAML 语法校验通过；
- 行为验证依赖后续真实场景：对有过 CHANGES_REQUESTED 的 PR push 新 commit，
  观察 gate 在复评落定前保持 pending、复评 APPROVED/CHANGES_REQUESTED 后
  由 review 事件写终态。

## Revisit

若 CodeRabbit 配额政策变化导致「复评永不落定」常态化，重新评估逃生阀的
便利性（如 allowlist 指定跳过复评的 label）。
