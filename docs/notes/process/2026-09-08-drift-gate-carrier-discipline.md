# Registry 批次收窗与 drift 留痕纪律成文（#1097 A 方案）

Status: implemented
Class: process

## Decision

issue #1097（2026-09-08 评估「drift 检测是否接 PR/CI」时发现）给出的三个候选
处置里，**A（零基建纪律成文）先行落地**：

- `docs/development/repository-workflow.md` 新增「Registry 批次收窗与 drift
  留痕」节：`ai_work.py drift` 只在 registry 宿主机有信号、CI runner 恒
  no-op 故不接入 PR/CI；批次收窗与合入核销/reconcile 前跑
  `python tools/dev/ai_work.py drift --strict`，有提示先处置（STALE 裁决 /
  声明补收窄 / overlap 改串行）再收尾；输出与处置结论随批次收尾评论留痕
  （#1035 Evidence 台账回溯组同载体）。

B（registry 宿主 cron 夜间 `check:full`）与 C（接 PR/CI）不随本单落地：
B 属宿主机 ops 配置、在 repo 外且本机兼生产宿主，保持开放项；C 的结构性
理由（CI runner 无 registry 数据、registry=visibility-only 非调度器、观测点
在 update/finish 交互循环而非合入时刻）已在 #1097 正文记录，此处不重复。

## Alternatives

- 在 #1097 内同时把 B（宿主 cron）也实现：放弃——repo 无 cron/schedule
  惯例，宿主配置超出仓库边界且须 ops 评估；纪律先行已能让收窗时段的
  drift 信号被消费，B 作为后续独立项。
- 把纪律写进 execution-contract.md：放弃——契约是 Execution 协议权威源，
  收窗纪律属仓库集成工作流（repository-workflow.md 域）；且契约文件在
  并行会话在窗 scope 内，避免撞文件。

## Verification

- `git diff` 核对仅两文件变更（repository-workflow.md + 本 note），无无关
  格式化；
- `gov-surface`（S1–S12）与 `ai-work --self-test` 直接调用全绿；ruff 通过；
- 本 Execution 经 registry declare→finish 全周期，scope 与实际 diff 一致
  （drift 零提示）；
- eslint/tsc/knip 属 frontend 检查（零 frontend 文件改动），/tmp worktree
  无 node_modules 不本地跑，CI required checks 覆盖。

## Revisit

- B（宿主 cron 夜间留痕）若需真正自动化再评估——触发的候选判据与 #1097
  记载一致（批次收窗纪律被执行但仍漏样本）；
- #1035 收窗（09-15 后）评论时首跑本条纪律，验证可执行性后如措辞有碍再修订。
