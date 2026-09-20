# S11 补「已发布脚本不可删改」锚（e82515 F5）

Status: proposed
Class: architecture

## Decision

Mode C 评审残留（#2546 / e82515 F5，#2842 未覆盖）仍成立：`AGENTS.md` 总原则
「已发布 `backend/agent/scripts/<name>/v<version>/` 不可原地修改或删除」**不在**
`## 硬不变量`，S11 原 11 锚不含该句——整句删除门禁不红。

本刀只做 F5 第一步（不升 ADR-0039、不改写该句语义）：

1. `HARD_INVARIANT_ANCHORS` 增第 12 锚（原文子串）；自测样例同步。
2. `2026-08-governance-surface-protection.md` S11 行：11→12 + 出处注记。
3. ADR-0039:16 纠「硬不变量」误标 → **总原则**（F-1 旁证；仍 Proposed，不做 Accepted / 不改 `AGENTS.md` 措辞）。
4. `2026-device-log-chain-contract` 关联行：Phase 2「阻塞笔记」→ 选项 A 已落地指针（ADR-0033 事实纠偏）。

## Alternatives

- 等 ADR-0039 Accepted 再挂锚 → 否决：F5 明写「立即补锚、与本单成立无关」；无锚则 Accepted 当日改写无机械义务。
- 把该句升入 `## 硬不变量` → 否决：属 0039 落地序，禁令「前置未齐不做」。
- 只改 ADR-0039 误标、不挂锚 → 否决：覆盖缺口仍在。

## Verification

- `python3 tools/dev/check_governance_surface.py --self-test`
- `python3 tools/dev/check_governance_surface.py --check`
- `python3 scripts/run_gates.py check:quick`
- 删掉 AGENTS 该句子串应 S11 红（自测「缺失」路径已覆盖同类）

## Revisit

- ADR-0039 Accepted 当日：**同 PR** 改写 `AGENTS.md` 该句 + 本 S11 锚串（F5 ②）。
- `frontend-feature-expansion` / 包存储 / Phase 3：触发条件未变，本刀不碰。
