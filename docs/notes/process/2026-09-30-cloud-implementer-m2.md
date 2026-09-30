# 协调域外云端实施者 M2 与对称前检

Status: proposed
Class: process

## Decision

按 [#3516 Owner 裁决](https://github.com/DUElost/stability-test-platform/issues/3516#issuecomment-5893263824)
落地 M2，不重新设计。#3540 已合入；本单 #3549 同步 ADR-0058 v1.3 D8/D10、
ADR-0034 v1.15、执行契约 v1.16 §3.6、工作流、adapter、根 AGENTS 与版本索引。

云端实施保持 1 Requirement → 1 Harness → 1 PR，不写当前本地 Registry。
本地实施者对称检查开放 PR / 远端分支后再 Registry 前检；Owner 对照本地
status --risk，可能重叠默认不并发，无重叠可并行，共享元文件串行。
云端首个有效改动形成后立即开 draft，不造空提交、伪实现或虚构 worktree 代登记。
按协调域而非代码类型划界；不改变复核/ready/激活闸门，不授予生产写权限。

受 S6 预算约束，契约 v1.15 的历史说明移入既有附录 A.4；adapter 的 Antigravity
历史汇总改为指向已有 ADR-0034 附录 A，保留当前职责表。不提高常驻预算，不增第二事实源。

## Alternatives

- M1 代登记不存在的 worktree：拒绝，会使 Registry 的 Git 派生事实失真。
- 远程 Registry / 新字段 / 标签 / CI gate / 自动路由：本轮无必要，沿用明确否决。
- 只让本地实施者查 Registry：漏掉域外 PR，所以必须对称前检。
- 按代码类型限制云端：不符合 Owner 已裁决的协调域边界。

## Verification

- 前检：origin/main 为 5bbe4228，#3540 已合入；唯一开放实现 PR #3548 不碰本单文件。
- Registry declare 使用单元 #3549；ai_work 的字段、状态机、Git 派生与本地落点未修改。
- 治理 self-test 通过；针对 origin/main 的 S1–S15 / S5x 全绿；check:quick 16 项通过。
  schema-at-head 无 DATABASE_URL 明确跳过，未作数据库验证。
- 常驻预算：根 AGENTS 79 行 / 6535 字节；契约 200 行 / 24366 字节；adapter
  88 行 / 8271 字节，均未提高预算。8 条根不变量和 S11 锚点保持不变。
- 独立复核返修：工作流顶部明确仅域内 status/declare/finish，ADR-0034 §2.1
  限定域内自行 declare；契约 §3.5 与 ADR-0058 D10-4 将域外实施者纳入开放 PR
  决策文档查重通道。全改动范围的现行正文对账未发现其余同类全员登记残留；
  历史实录不回改。返修后受限 scope 中 check:quick 16 项通过，schema 探针跳过。

## Revisit

- 云端与本地并发若出现真实可见性缺口，再由 Owner 裁决显式 remote/scope-only
  模型或共享协调入口，不把 M1 当默认升级路径。
- 文档合入不是云端流程 dogfood，也不能代替 #3516 的 CLI/IDE 加载矩阵验收。
- 独立复核前保持 draft；本实施会话不得自我复核。
