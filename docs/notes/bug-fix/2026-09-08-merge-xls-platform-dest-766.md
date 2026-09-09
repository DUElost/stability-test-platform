# 双平台 merge xls 同名进 jira bundle（#766）

Status: implemented
Class: bug-fix

## Decision

`run_extract_sync` 复制 `merge_result_xls` 时，若 `dest.exists()` 则 `continue`，
mtk/unisoc 同为 `Result_MergeFiles.xls` 时后一份静默丢失。

1. URI 含 `/merge/{mtk|unisoc}/` 时落盘到 `jira/{run}/merge/{platform}/`，
   basename 不再冲突。
2. 遗留扁平 URI 撞名时改用 `_{platform}` 后缀并 WARNING；仍冲突则
   `merge_xls_skipped_same_name` 计数（不再静默）。
3. `run_context.extract` 增加 `merge_xls_skipped_same_name`。

## Alternatives

- 仅打日志仍跳过：可观测但 bundle 仍缺报告，不满足验收。
- 强制所有历史扁平产物迁到子目录：破坏既有消费路径，仅对新分区 URI 用子目录。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest \
  backend/tests/services/test_dedup_extract.py -q \
  -k 'dual_platform_merge or legacy_flat_merge'
```

## Revisit

Jira 消费方若仍只扫 `jira/{run}/*.xls`，需同步认 `merge/{platform}/`；
或另开 UI/文档说明。
