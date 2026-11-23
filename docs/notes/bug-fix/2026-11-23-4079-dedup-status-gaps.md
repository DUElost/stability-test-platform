# Agent Note: NOT_READY/NO_INPUT 下保持 delivery_view.gaps 与 delivery_version.gaps 一致 (#4079)

## Decision

修改 `backend/services/shadow_delivery.py` 的 `evaluate_delivery_status()`：在校验 `publish_status` 后即统一计算当前已知的 `gaps`（`coverage_missing` + `evidence_incomplete` + `publish_failed`，按 ADR-0062 D9 第 3 条仍排除 `RETRYING` 项），并在 `NOT_READY`（包括 `latest_merge_no_input`、`latest_merge_failed`、`effective_coverage_empty`、`representative_retrying`）与 `SCOPE_ONLY` 返回分支同步携带该 `gaps` 元组。由此 `partition_scope.delivery_view` 与数据库已登记的 `delivery_version.gaps` 口径一致。

## Alternatives

- **前端单独从 `coverage.missing` 拼装**：会导致 `delivery_view.gaps` 与持久化 `delivery_version.gaps` 语义裂口持续存在，非覆盖类缺口（`evidence_incomplete`/`publish_failed`）仍会在 `NOT_READY` 下消失。

## Verification

- `backend/tests/services/test_shadow_delivery.py` 新增 `test_not_ready_no_input_preserves_coverage_gaps_matching_snapshot`。
- 运行 `test_shadow_delivery.py`、`test_b8_g7_partition_consumers.py`、`test_b8_g6_evidence_publication.py`、`test_b8_g8_failed_delivery.py` 与 `ruff`/`compileall`。

## Revisit

- 生产激活 `DEDUP_FIRST` 后，复核 `NO_INPUT` / `RETRYING` 分区在 `DedupReportCard` 的缺口展示一致性。
