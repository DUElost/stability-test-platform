# external-tools Living 设计：Phase 2/3「零启动」排期口径勘误

Status: proposed
Class: architecture

## Decision

`docs/design/2026-09-external-tools-integration-and-package-architecture.md` §5 排期口径仍写「Phase 2/3 零启动」（dated 2026-09-10 / ADR-0033 v1.2）。同文 §4 与 ADR-0033 v1.5 已登记 **Phase 2 选项 A 落地**（B5 `DedupMergeEngine`）。将该句改为：Gantt 原时间点作废保留；**Phase 2 选项 A 已落地**；**Phase 3 / 包存储仍未启动**（§5.4 未触发）。不实现包存储，不改 ADR-0033 正文（并行 #2856 占用）。

## Alternatives

- 改 ADR-0033 §4「时间点…零启动」历史作废注 → 否决：该句描述 v1.2 作废 Gantt 的原因，且文件被 #2856 占用。
- 顺手改 historical notes（`2026-09-15-center-storage…`）→ 否决：提案快照，非 Living 权威。
- 借机推进 Phase 3 / 包存储 / Jira 全迁 → 否决（禁令）。

## Verification

- `rg 'Phase 2/3 零启动' docs/design/2026-09-external-tools-integration-and-package-architecture.md` → 0
- 同段同时出现「选项 A…已落地」与「Phase 3 与包存储仍未启动」
- `python3 scripts/run_gates.py check:quick`

## Revisit

- ADR-0033:147 历史作废注若需与「选项 A 已落地」并列澄清，等 #2856 合入后独立小刀。
- 包存储 / Phase 3：§5.4 触发后再排期。
