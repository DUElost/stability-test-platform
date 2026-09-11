# 审查覆盖与修复有效性审计报告（R01–R15 之后）

Status: implemented
Class: process

## Decision

- 新增审计报告
  [`REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41.md`](../../reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fdca41.md)：
  对「R01–R15 逐区审查 + issue 驱动修复闭环」的独立只读审计（基线 `fb87d5f1`），
  结论三块——覆盖缺口（跨区收口未启动 / 覆盖证据链缺失与总纲漂移 / 无主候选面）、
  已闭环修复的量化与可持续性风险（类级复发、Revisit 无收割、验证后置）、
  闭环脆弱点（FIFO 单车道 #1246 / 审批与清理边界 #1293/#1294）。
- 报告以 `CA-*` 稳定编号组织发现，并列出 `CA-D01–CA-D07` 待裁决项（各带建议），
  供后续多 Harness 综合审查（Mode C：先独立、后汇聚）作为输入；本报告不预设裁决结果。
- **v1.1 增补（本单）**：报告新增附录 B「止血盘点与组合治理」——三层止血盘点
  （良性 / 出口悬空 / 未标注）、7 个组合治理簇矩阵、4 项 ADR 级判定（队列协议新立；
  ADR-0017 与 ADR-0008 增补；测试/生产隔离组合单）与 `CA-D08–CA-D12` 待裁决项；
  为 `CA-Q02`/`CA-D04` 提供实证清单。
- 流程说明：原报告经 PR #1307 合入（`4a955874`）后，按契约 T9（MERGED 拒绝返工）
  重新 `declare` 本 Execution，以增量 PR 落地 v1.1；报告本体不新建第二份。
- 在 [DOC-MAP](../../DOC-MAP.md) 登记本报告（Living 审查层），与既有审查文档索引一致。
- 不修改业务代码、ADR 或执行语义；不关闭/新建 issue；「总纲漂移」等修订建议
  以 `CA-D*` 待裁决项形式提出，不在本单内实施。

## Alternatives

- **只留在会话/不落文档**：不采纳。综合审查需要可引用的稳定编号与证据面，聊天记录不可复用。
- **直接修订 `PROJECT_REVIEW_PLAN.md`（补 §5 登记、改覆盖模板）**：不采纳。
  交付形态是方向级选择，按 execution-contract §10「契约先行」应先裁决再修订，而非审计单内顺手改。
- **由本单直接开/关 issue**：不采纳。#1246、#1293、#1294 等已有承载；新增面（类级修复规则、
  Revisit 收割）应由综合轮裁决后决定载体，避免与在窗审计（codex，scope=`docs/reviews`）冲突。
- **把审计写成逐条复述 issue**：不采纳。报告只保留聚合事实、判定与裁决输入，明细以既有 issue 为准。

## Verification

- 通过：`.venv/bin/python scripts/run_gates.py check:quick`，7 项门禁全部通过
  （ruff / eslint / tsc / knip / compileall / gov-surface / ai-work）；**v1.1 增补后重跑同样全绿**。
- 通过：报告与 Note 的相对链接均指向仓库内实存路径；DOC-MAP 链接有效（含 v1.1 增补后校验）。
- 通过：`git diff --check` 与 `git status` 审阅，本次变更仅涉及本报告、本 Note 与 DOC-MAP 一行；
  未包含凭据、无关格式化或本地 Harness 状态。
- 口径声明：报告内全部计数为 2026-09-11 观测值并附只读复现命令；未运行 pytest/Vitest/迁移/部署，
  未做目视验证，未触发 CI。文档变更不涉及行为语义，不进行运行时验证。

## Revisit

- 综合审查轮完成汇聚、`CA-D*` 裁决落地后，本报告降为历史证据：只登记去向下场，不更新结论。
- 平行审计（codex）产出与本报告冲突或互补时，由综合轮统一裁决；本报告不自行修订。
- 观测指标（PR / Registry / issue 计数）随时间失效；若后续轮次复用，须重跑附录复现命令而非引用旧值。
