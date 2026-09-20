# Ownership 索引 Draft→Living + N6 残留收口

Status: proposed
Class: architecture

## Decision

#2546 Closed 后，对 `docs/design/2026-semantic-ownership.md` 做最小文档收口（不扩表、不升 frontend TBD、不开新 Contract）：

1. **状态 Draft → Living**：S15 已在 `main` 生效，继续标 Draft 与 DOC-MAP/运行态不一致。
2. **补 N6 / F-07 Domain Authority 注记**（Accept-with-nits 明确未收、仓内仍成立）：`execution-contract` 的「以本文为准」资格来自操作规格，**不可**被本索引援引为自我授权先例。
3. **§9 纠过时表述**：删「不实现 0033 Phase 2」——Phase 2 选项 A（B5 薄 `DedupMergeEngine`）已由他单落地（ADR-0033 v1.5）；非目标收窄为包存储 / Phase 3 / 设备端样板。
4. 同步 `docs/DOC-MAP.md` 设计行 **Living**。

## Alternatives

- 继续留 Draft「可接受过渡」→ 否决：关单后 Draft 误导路由面，且 N6 注记与 §9 过时句已是明确未收/不一致。
- 借机升 `frontend-feature-expansion` 实锚 → 否决（触发未至）。
- 升 ADR-0039 Accepted / F-1 同步 `AGENTS.md` → 不做：0039 仍 Proposed，#735 退役写操作未完成，落地序 §4.3 未齐。
- 新开专域 Living Contract → 否决（选刀禁令）。

## Verification

- `python3 tools/dev/check_governance_surface.py --check`（S1–S15、S5x）
- `python3 scripts/run_gates.py check:quick`
- 文内无「冲突时以本文为准」自我授权；§0 含 Domain Authority 注记；§9 不再声称「不实现 Phase 2」

## Revisit

- `frontend-feature-expansion`：触碰 ADR-0013 / 前端 IA 时再升实锚
- ADR-0039：Accepted 前置齐（#735 退役闭环 + owner 裁决）后再做 F-1
- 包存储 / Phase 3：§5.4 触发后再排期
