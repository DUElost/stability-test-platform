# #1819 reconciler pull-failed DLE 漏改调用点

Status: implemented  
Class: bug-fix  
Issue: https://github.com/DUElost/stability-test-platform/issues/1819

## Decision

`#1719` 将 `_register_pull_failed_device_log_event` 拆为 `_build_pull_failed_payload`（只组装）+
`post_event_payload`（发送），但 `_handle_pull_failed` 仍调用已删除的旧方法，每次拉取失败必抛
`AttributeError` 并被外层 except 吞掉。修复为与 `_ensure_emit_for_intent` 一致的两步调用，并补
带非 None `device_log_client` 的回归用例。

## Alternatives

- 恢复 `_register_pull_failed_device_log_event` 包装器：与 #1719 意图簿拆分方向不一致，不采用。
- 仅改方法名不重测：无法防止同类漏改再次被 broad except 掩盖。

## Verification

- `python -m pytest backend/agent/tests/test_aee_reconciler.py::test_handle_pull_failed_posts_device_log_event_when_client_present -q`
- `git grep _register_pull_failed_device_log_event` 应无匹配

## Revisit

无。若 pull-failed 也需 emit 意图簿补偿，属 #1719 后续范围，本修复仅恢复 #1044 DLE 契约。
