# ADR-0050 起草：audit 保留期与 ADR-0044 D3 安装证据的对齐（#2789）

Status: implemented
Class: bug-fix

## Decision

起草 **ADR-0050 v0.1（Proposed）**，把 #2789 发现的跨 ADR 未定义点变成显式裁决
对象：ADR-0044 D3 说「审计是安装状态的持久证据」，ADR-0049 给了 `install_agent*`
90d 的默认视界，两者各自成立、合起来「持久」没有定义。三选项：**丙=明示接受
90d 视界（推荐，零迁移）**、甲=入 security 层（稀释语义轴）、乙=事实源迁移专表
（超前建设）。owner 三选一后才动实现；本 PR 只交付裁决材料。

## Alternatives

- 直接改 ADR-0049 到 v1.1 挂 D6（Proposed）：会把已 Accepted 且已部署的 D1–D5
  整体拖回 Proposed，与其实现已在生产运行的事实矛盾——弃。
- 只在 #2789 评论里写权衡、不动 docs：方向级决策按 AGENTS.md 须走 ADR，且
  issue 评论不进 adr/README 索引面、S12 无法约束后续同步——弃。
- 顺手修 `audit_log_cleanup` 汇总审计「已删却报 0」的读数瑕疵：那是实现瑕疵不是
  保留期裁决，混进决策 PR 会扩大裁决面——留 #2789 正文记录，裁决后随实现修。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`（S10/S12 状态与
  索引一致性）通过；
- `venv/bin/python scripts/run_gates.py check:quick` 通过；
- docs-only：无代码/测试变更，`git diff --stat` 仅 4 个 docs 文件。

## Revisit

- owner 三选一后：ADR-0050 转 Accepted 并按选定项补实现（丙=仅 docstring；
  甲=`SECURITY_ACTIONS` 一行；乙=专表+迁移+console 写点）；
- 产品提出安装历史查询需求 ⇒ 乙回归为正解（ADR-0050 §4 已预埋出口）。
