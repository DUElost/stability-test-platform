# extract 残缺 dest 不得误标 ARCHIVED（#1070）

Status: implemented
Class: bug-fix

## Decision

事件目录改为 staging 复制 → 写入 `.stp_extract_complete` → `rename` 原子发布。
`dest.exists()` 仅在完成标记存在时视为已交付并 ARCHIVED；无标记的半成品
`rmtree` 后重拷。复制异常时清理 staging，不留下可被「存在」误判的 dest。

涉及：`backend/services/dedup_extract.py`；回归于
`backend/tests/services/test_dedup_extract.py`。

## Alternatives

- 失败时只删 dest、仍直写目标：并发/崩溃窗口仍可能留下半成品；staging+标记更稳。
- 以文件数/checksum 推断完整：成本高且与源契约弱；放弃。

## Verification

- `test_run_extract_sync_replaces_incomplete_dest_without_marker`
- `test_run_extract_sync_failed_copy_does_not_archive_or_leave_complete_dest`
- 既有 `test_dedup_extract.py` 矩阵
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_dedup_extract.py -q`

## Revisit

存量 jira 目录无完成标记时，下次 extract 会重拷（多一次 I/O，语义正确）。
若需免重拷，可另开一次性回填标记工具。
