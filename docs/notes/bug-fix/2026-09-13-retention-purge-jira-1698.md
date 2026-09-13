# retention NFS 回收补 jira/{run_id}/（#1698）

Status: implemented
Class: bug-fix

## Decision

`purge_run_storage_dirs` 枚举由 `("devices", "dedup")` 扩为含 `"jira"`。
extract 产物落在 `nfs_root/jira/{run_id}/`；漏清会使行删后目录失去唯一
索引、永不可回溯。

## Alternatives

- **另起 cron 扫孤儿 jira/**：无法可靠关联 run_id，且与「先文件后行」自愈
  模型脱节；否决。
- **只改文档人工清理**：不堵盘满链；否决。

## Verification

- `python -m pytest backend/tests/scheduler/test_retention_cleanup.py -k nfs -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若新增其它按 run_id 分桶的 NFS 前缀，应同步扩枚举并在 `_make_nfs_dirs`
覆盖。
