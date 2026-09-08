# 手动 scan/merge 接入轮次编排（#1077）

Status: implemented  
Class: bug-fix

## Decision

手动 `POST .../dedup/scan` 不再只 `emit scan_now`：改为
`enqueue_dedup_terminal_async`，由 SAQ `scan_task` 负责下发、轮询登记与
upload/merge 后继链；响应仍返回 ONLINE host 预览，并带 `enqueued: scan_task`。
无 ONLINE host 时 409，避免空轮次入队。

手动 `POST .../dedup/merge` 经 `resolve_manual_merge_round` 解析轮次后调用
`run_merge_all_platforms_sync`：优先最新非空 `scan_round_id`；否则用 scan
产物 `min(created_at)` 作为 `round_started_at`。不删除无轮次保护。

涉及：`backend/api/routes/dedup.py`、`backend/services/dedup_scan.py`；
测试见 `test_dedup_scan_endpoints.py`、`test_dedup_scan_merge.py`。

## Alternatives

- 端点内自行 emit + 登记 + enqueue upload：与终态路径分叉，易再漂移。
- 删除 `_load_org_files_for_merge` 无轮次拒绝：会合并全部历史，明确禁止。
- 手动 merge 要求调用方传 `scan_round_id`：UI 无该字段，服务端推导更稳。

## Verification

- `test_scan_enqueues_scan_task_for_online_hosts`
- `test_scan_rejects_when_all_hosts_offline`
- `test_merge_passes_resolved_round_to_merge_all`
- `test_resolve_manual_merge_round_*`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/api/test_dedup_scan_endpoints.py
  backend/tests/services/test_dedup_scan_merge.py -q`

## Revisit

若前端需要「仅预览 host、不入队」，可另加 dry-run query；当前手动即等于入队。
