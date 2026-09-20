# 丙案落地：审计保留期与安装证据的视界对齐成文（#2789 / ADR-0050 v1.0）

Status: implemented
Class: bug-fix

## Decision

owner 2026-09-20 在 #2789 三选一裁决**采丙（明示接受 90d 视界）**，本 PR 落地
裁决面：ADR-0050 Proposed v0.1 → **Accepted v1.0**（§2 改写为决策与依据、甲/乙
移入未采纳）；ADR-0044 → **v1.1**（D3 补「持久 = 审计保留期视界内」的视界注）；
ADR-0049 关联行同步裁决结果；`hosts.py::_latest_install_audits` docstring 标注
同一视界（纯注释，零行为变更）。零迁移、零代码语义改动——这正是丙案的全部成本。

## Alternatives

- 顺手改保留期默认值（90→180 或 install 入 security 层）：那是甲案，owner 未选；
  且实现侧任何一处被「顺手放宽」都会让 ADR-0049 D1 的分层语义轴继续漂移——弃。
- 把顺带项（汇总审计「已删却报 0」读数瑕疵）一并修在这里：qwen 的 PR #2833 已
  合入 main（`e0b8c70a`），重复实现必撞车——本 PR 不含，只保留 ADR/docstring 面。
- 只回 issue 评论不动 ADR：已 Accepted 的 ADR-0044 的语义补注按仓库纪律必须走
  版本化修订（v1.1），S12/S14 才能约束后续同步——弃。

## Verification

- `check_governance_surface.py --check`（S10 头格式 / S12 头部↔README 状态与版本 /
  S14 注释 ADR 版本引用）通过；
- `run_gates.py check:quick` 12 gates 全绿（hosts.py 仅 docstring，行封顶/inner-import
  无影响）；
- 变更面核对：`git diff --stat` = 6 文件（4 docs + hosts.py docstring + 本 note）。

## Revisit

- #2789 在实现合入后关闭（若 bot 合入不触发原生关单则手动关）；
- 产品提出安装历史查询需求 ⇒ ADR-0050 §5 的乙案触发条件生效，连同 console 写点
  重新设计并修订 ADR-0044 D3。
