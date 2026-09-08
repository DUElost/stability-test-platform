# 事件上送中心路径按 event_id 隔离，避免同名互删（#1073）

Status: implemented
Class: bug-fix

## Decision

`EventUploader._upload_one` 对已绑定 PlanRun 的目标从
`devices/{plan_run_id}/{src.name}` 改为
`devices/{plan_run_id}/{event_id}/{src.name}`，与
`devices/unassigned/{event_id}/` 对齐。checksum mismatch 的 `rmtree` 只落在本
事件目录；extract 继续只消费 DLE `remote_path`，不依赖扁平 basename。

涉及：`backend/agent/event_uploader.py`、`backend/agent/aee/paths.py` 注释、
`backend/agent/tests/test_event_uploader.py`。

## Alternatives

- 路径拼 `serial`：同机多事件仍可能撞 basename；event_id 更稳。
- mismatch 时拒绝覆盖、不改路径：旧布局碰撞仍可能卡上传；根因未解。

## Verification

- `test_same_basename_different_events_do_not_overwrite`
- `test_upload_one_marks_remote` / `test_missing_local_patches_remote_when_remote_present`
- `TESTING=1 JWT_SECRET_KEY=test-secret DATABASE_URL=sqlite:///:memory:
  python -m pytest backend/agent/tests/test_event_uploader.py -q`

## Revisit

存量扁平 `devices/{plan_run_id}/{basename}` 仍可由历史 `remote_path` 提取；
无需强制迁移。若运维脚本仍按 depth-1 枚举事件目录，需改读 DLE 或接受
`{event_id}` 中间层。
