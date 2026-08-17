# 合入路径注意力预算（核心原则）

Status: implemented
Class: process

## Decision

单人项目的第一资源是注意力。CI/流程设计的第一约束：**合并路径上的阻塞
检查保持 ~2 分钟内**（当前 required checks 为 lint / CodeQL / pr-typecheck
/ pr-compileall / pr-agent-tests，全绿即 auto-merge；`code-rabbit-gate`
同为 required check，由 merge-gate 写状态但语义是 best-effort——仅当
CodeRabbit 对当前 head 给出终态决策时构成阻断）；任何引入等待或分心
（要惦记、要切回来看结果）的检查一律放异步路径（夜间批量全量 CI 兜底）。

不得为「合并前验证」加长合入等待。据此否决过：

- Merge Queue：每个 PR 等一轮全量 CI（实测 ~9 分钟）才合入；
- vitest / backend-test 前置为 PR 必查项；
- backstop 从 nightly 改 hourly：全量 CI 随时可能白天触发，破坏可预测性。

人工评审不违反本原则：它发生在「看这个 PR」的同一个注意力块内，是同步
动作，不是等待（例：frontend-major / github_actions 更新的人工评审）。

## Alternatives

「合并前全量验证」路线（Merge Queue / PR 全量）：否决。合入延迟换来的
「main 恒绿」对单人收益低；失败信号延迟到次日早晨是可接受的刻意成本。

## Verification

原则本身无代码验证；违反本原则的 CI 提案应被直接否决（新会话的 agent
读本文后不应再提 MQ / 前置全量 / 高频兜底类建议）。

## 补充（2026-08-16，#281 评审 P2）

`pr-backend-test`（PR 阶段跑 `backend/tests/`，PG service）作为**信息性、
非阻塞** job 加入 ci.yml：不进入 required checks，不延长合入等待，但把
控制面后端回归（如密码长度变更）提前暴露在 PR 上。这是对 #281 评审
「PR 门禁未覆盖改动影响的控制面后端测试」的回应，且不违反本原则——
被否决的是「backend-test 前置为 PR 必查项」，信息性信号不在否决范围内；
若未来要把它升为 required，须先重评本原则。

## Revisit

若未来出现第二维护者或外部贡献者，「合并前全量验证」的价值排序需要重评
（届时再议 Merge Queue）。
