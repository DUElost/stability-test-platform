# gov 行为 eval（L1）移除

Status: implemented
Class: simplification

## Decision

移除治理面防护 L1 层的行为 eval（`tools/dev/run_gov_evals.py` 211 行 +
`tools/dev/gov_evals_cases.yaml` 72 行，2026-08-26 由 PR #453/ca9f0855 引入），
作为 ADR-0034 合入前的低价值机制清理。同一 PR 先行落地 **S11 硬不变量锚点
检查**（`check_governance_surface.py`，11 条锚串）承接其主要防线，main 上
防护不出现空窗（S11 commit 在前，本删除 commit 在后，可按 commit 粒度回滚）。

移除依据（2026-09-06 评估，能力分解论证）：

1. **独占价值可分解**：L1 两项独占能力均能以更便宜基元承接——不变量保全
   （整条删除/改写检测）→ S11 确定性锚点（零 LLM 成本、进 CI、覆盖 8/8
   硬不变量，原 case 仅约 5/8）；事故分诊（「没写到 vs 写了没传导」）→
   S11 先答「文本在不在」+ 需要时单问手跑 `claude -p` 鉴别。
2. **维护税真实且无守卫**：每次治理面知识迁移须同步 case（#853 已交过一次：
   删 2 条、重指 source），无门禁强制该同步，遗忘即陈年烂 case。
3. **环境死重**：答题引擎=本地 `claude` CLI，09-05 起故障不可用且无修复排期；
   在唯一部署环境跑不了的防护层实际防护为零。
4. **多 Harness 时代形状错配**：单引擎金丝雀只覆盖 Claude Code 一条摄取
   路径；若 ADR-0034 后 auto mode 需要行为门禁，正确形态是引擎可插拔重建
   而非保留本工具。

实证记录（保留供重开时参考）：08-27 曾 **12/12 全绿**并作为常驻瘦身执行
协议的安全网（`docs/reviews/RESIDENT_CONTEXT_AUDIT_2026-08-27.md` §5 第 0/3
步）；09-05 #853 后因 CLI 故障未再跑成，post-#853 布局的行为验证空白由
S11 的文本级验证部分弥补。

接线清理：`run_gates.py` 删 `gov-evals` 条目、`check:gov` 改为
`[gov-surface, gov-skills]`；`check_governance_surface.py` 的
`GATE_TO_CI_ANCHOR` 删 `gov-evals` 映射行；设计文档 §2/§4/§7 标注 Removed。
**#825 的 gov-evals 半边随本变更消解**（gov-skills 半边与其自身的本机转录
路径问题仍在原 issue 跟踪）。

部分取代（按取代纪律交叉链接，原文留档）：`2026-08-governance-surface-protection.md`
§2 挂载表/§7（L1 部分；L0/S7/gov-skills 部分仍有效）、
`2026-08-26-governance-surface-protection.md`（L1 条目）。残余缺口（语义
传导验证/标准化分诊/多 Harness 摄取覆盖）由 **#855** 跟踪，主触发条件
= ADR-0034 合入完成后以更高价值机制补全。

## Alternatives

- **保留现状（按需手跑）**——放弃：在唯一环境 CLI 故障不可用，保留即
  无限期死重 + 虚假安全感（文档描述与实际能力脱节）。
- **先修 CLI 跑通 post-#853 基线再决定**——放弃：修复无排期，会把
  ADR-0034 前置清理阻塞在无人认领的环境问题上；且能力分解论证成立后，
  基线信号的边际价值不改变结论。
- **扩展 `--engine` 为多引擎实现后保留**——放弃：设计文档 §7 的「扩展位」
  在代码中从未实现（仅报告头字符串标签）；为保留而先扩建与「清理降复杂度」
  的目标相反。正确时机是 #855 触发时按需重建。
- **仅删工具、不动设计文档**——放弃：违反取代纪律；Living 文档描述与
  仓库实态不一致正是本项目治理面要防的漂移形态。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --self-test`
  ——12 条规则红/绿双向（含 S11 新增三样例：齐全/全缺/单条改写）；
- `venv/bin/python tools/dev/check_governance_surface.py --check`
  ——S1–S11+S5x 全绿（S5x 验证改后的 run_gates 与 GATE_TO_CI_ANCHOR
  一致性、S10 验证本 Note 头部、S11 验证 11 锚串对当前 AGENTS.md 全命中）；
- `venv/bin/python -m ruff check tools/dev/check_governance_surface.py
  scripts/run_gates.py` 通过；`python -m py_compile scripts/run_gates.py` 通过；
- 残留引用 grep：`run_gov_evals|gov_evals` 在非 archive/reviews/notes 路径
  零命中（历史留档按纪律保留原文）；
- 未运行项（pending）：`check_gov` 实跑（含 gov-skills 探针）依赖本机
  转录数据，未在本变更中执行；PR CI 六项 required checks 以实际运行为准。

## Revisit

- **#855 的三个解冻条件**：ADR-0034 合入完成（主路径）；本机 Claude CLI
  修复排期；出现真实「写了没传导」事故且单问手跑不够用。
- 恢复锚点：本 PR 的删除 commit（合并后 hash 回填至 #855）；恢复 =
  取回 2 文件 + `run_gates.py` 三处接线 + `GATE_TO_CI_ANCHOR` 一行。
- S11 锚点表随 AGENTS.md 硬不变量措辞演进须同步维护（有意改写必须连锚
  一起改——这是设计意图，不是负担）。
