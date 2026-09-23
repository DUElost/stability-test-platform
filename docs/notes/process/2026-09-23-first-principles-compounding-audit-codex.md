# 第一性原理与长期复利审计落盘（Codex CLI）

Status: implemented
Class: process

## Decision

将 150 host / 3750 device 目标下的只读静态审计写入
[`STP_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_codex.md`](../../reviews/STP_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_codex.md)。
报告以 `main@8bc6bc1e` 为基线，区分代码事实、条件模型和待测运行态；独立于同日
`ae232a30` 基线的并行审计稿。用户给出的新容量上限作为目标，不把旧文档数字差异立为缺陷。

## Alternatives

- 只输出聊天摘要：不采纳；用户明确要求 Codex CLI 产出仓内 Markdown 文档，审计证据
  需要可复核的持久路径。
- 直接修改并行 Claude 稿：不采纳；其文件尚未跟踪，且基线、归属和部分结论不同。
- 将本轮建议直接写为 ADR 或实施变更：不采纳；连接预算、长期统计等方向仍需裁决和
  运行证据，审计报告不能替代决策。

## Verification

- 只读核对 `main@8bc6bc1e` 的数据库连接池、部署 PG 上限、产物队列、保留清理、
  脚本执行器与包注册、设备和结果读路径及相应 ADR。
- 与同日并行审计稿交叉核对，纠正 ADR-0051 Phase 3 状态和将条件模型写成现场吞吐的口径。
- 在独立 worktree 执行 `.venv/bin/python -m scripts.run_gates check:quick`：15 项门禁通过；
  其中 `schema-at-head` 因该 worktree 未配置 `DATABASE_URL` 而跳过数据库对齐检查。
  新增 Markdown 的本地链接逐一解析通过，`git diff --no-index --check` 无空白问题。
- 额外执行 `check:gov`：`gov-surface`、`gov-skills` 通过；`harness-ingest` 的首个
  Claude 探针超时，第二个探针运行时主动中止，因此 `check:gov` 未完成。
- 共享主检出此前同一门禁被并行未跟踪的 Claude 审计稿断链挡住；独立 worktree
  不包含该稿，本 PR 不修改它。
- 未运行容量测试、生产诊断、真机联调或恢复演练。

## Revisit

ADR-0047 裁决、产物丢弃对账、恢复演练、工具适配器试点或 150/3750 阶梯压测
取得新证据时，逐项修订报告；阶段性模型数据必须让位于实测分布。
