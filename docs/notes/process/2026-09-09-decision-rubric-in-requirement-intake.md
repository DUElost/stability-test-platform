# 需求入口嵌入决策三问（第一原理 / 复利口径）

Status: implemented
Class: process

## Decision

把「第一原理 + 长期复利」从**隐性裁决口径**提升为**需求入口的显式清单**，
落点为既有模板空位而非新增文档：

- `.github/ISSUE_TEMPLATE/feature_request.md`「背景与动机」下增三问：
  本质问题 / 最小方案 / 不做会怎样·复用预期；
- `.github/ISSUE_TEMPLATE/epic.md`「概述」下增同构三问（第三问改述为
  「可沉淀为通用能力的部分」）。

**不新增任何哲学文档，不改 `AGENTS.md`/`CLAUDE.md`。** 决策依据：该口径在
本仓库已运行且有实证——`docs/notes/process/2026-09-07-855-enforcement-map.md`
（按第一原理与复利裁决不重建行为验证层）、
`docs/design/2026-08-governance-surface-protection.md:115-118`（行为测量随模型
版本作废=负复利 vs 确定性 gate 随 PR 复利）、
`reviews/AI_NATIVE_SDLC_PLAYBOOK_COMPARISON_2026-08-26_synthesis.md` §3
「刻意不追」（正式负向决策 + 重议触发条件）。缺的不是哲学，是**需求阶段
（纠偏成本最低处）的追问入口**。

边界（有意不扩）：不动 `pull_request_template.md`——PR 阶段方向已定，
再加三问是纯仪式（每 PR 固定负担，收益接近零）。

## Alternatives

- **新增「项目开发哲学」文档（常驻或按需）**：否决——常驻路线撞
  `check_governance_surface.py` S9 根章节白名单（`AGENTS.md` 只允许
  `{总原则, 硬不变量, 开始任务时, 按需入口, 提交前}`）与 S6 预算
  （当前 70 行/4685 B，上限 80 行/8KB）；改门禁 + 同步 S11 锚点表是为一段
  宣言付**负复利**。按需路线（`docs/` 新文档）无入口触发点，等同无人读。
- **PR 模板同步加三问**：否决——见上「有意不扩」；且 PR 描述已被
  「动机 / 关联 Issue」与文档清单占满。
- **季度复盘哲学是否有效**：否决——本仓库已有更快反馈环：
  「刻意不追」每项自带重议触发条件（如「团队规模 ≥3 人常驻」），
  触发即重议、不触发不打扰，比日历复盘精确且零常驻成本。
- **把三问做成门禁（issue 模板字段必填/CI 校验）**：否决——issue 正文无法
  机械判定语义质量，强制只会生产套话；模板是提示而非校验面。

## Verification

- `venv/bin/python tools/dev/check_governance_surface.py --check`（模板不在
  S1–S12 检查面内，但共享治理面变更须全绿）；`--self-test` S1–S12 双向验证；
- `venv/bin/python scripts/run_gates.py check:quick` 全绿；
- 两模板 diff 人工复核：仅新增 3 行 bullet，未动既有字段与 frontmatter；
- 跟踪 issue：[#1183](https://github.com/DUElost/stability-test-platform/issues/1183)。

## Revisit

判据（#1267 补充；触发任一即重议，重议后另开 issue，不回填本 Note）：

1. **症状 A「填了不看」**（可检索）：新 issue 的三问答案高度同质（一律
   「提升效率 / 先做这个 / 复用高」），或出现「待补 / 无 / 不适用」等占位式
   答案。→ 降级为注释行或移入 `docs/notes/README.md`（提示语留痕即够）。
2. **反向事故**：出现「按三问否掉、事后证明该做」的实例。→ 第三问从
   「复用预期」改为显式的「不做会怎样（可验证的损失）」，并考虑在
   `.cursor/rules` 增一条需求评审提示。
3. **规模前提变化**：团队规模 ≥3 人常驻时，与「刻意不追」中「强制 code
   owner 审批」同批重议（两者前提相同：单人主路径）。

观测方式：`gh issue list --state all --search "本质问题"` 抽样读新 issue 正文。
**无自动门禁**——#855 裁决"测量不产生约束力"，不为低频事件建常驻设施；
本项的判据依赖人工抽样，属 residual。
