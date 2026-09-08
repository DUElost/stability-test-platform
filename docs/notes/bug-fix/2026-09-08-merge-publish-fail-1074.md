# 中心存储发布失败不得回退本地当成功（#1074）

Status: implemented
Class: bug-fix

## Decision

`_publish_merge_to_center` 在中心已配置（`STP_AEE_NFS_ROOT`）但
`mkdir`/`copytree` 抛 `OSError` 时改为抛 `RuntimeError`，不再 `return None`。
`run_merge_sync` 仅在发布返回 `None`（未配置中心）时回退登记工具本机路径；
发布失败不登记、不返回 `"ok"`，SAQ `merge_task` 可按异常重试。

涉及：`backend/services/dedup_scan.py`；测试见 `test_dedup_scan_merge.py`。

## Alternatives

- 返回三态枚举（skip / ok / fail）：调用面更宽，现有 `Path | None` + raise
  已够区分。
- 登记本机并标记 `storage_uri` 临时态：引入新契约与清理责任，超出本 bug
  最小修复。

## Verification

- `test_raises_when_center_copy_oserror`
- `test_run_merge_sync_raises_on_center_publish_oserror`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_dedup_scan_merge.py -q`

## Revisit

生产未挂中心时仍走本机回退；若日后强制中心为交付前提，可改为未配置即失败。
