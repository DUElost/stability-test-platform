# 控制面 scan 工具 env 角色键分离（#295）

Status: implemented
Class: bug-fix

## Decision

#295（DEVICE_LOG_FLOW_REVIEW_2026-08-09 §二 P2-1）：控制面与 Agent 此前都读
无前缀 `STP_DEDUP_SCAN_PYTHON/_SCRIPT`，同键不同值，共用 `.env` 的部署会角色
混淆。

- 控制面 `resolve_scan_tool`（`run_merge_sync` / `build_merge_argv` 唯一入口）
  改读 `STP_BACKEND_DEDUP_SCAN_PYTHON/_SCRIPT`；
- **平滑过渡**：新键未配置时回落旧无前缀键并打 WARNING
  （`scan_tool_legacy_env_fallback`），生产 `.env.backend` 迁移完成前不中断
  merge 链；
- Agent 侧保持读无前缀键（`scan_runner.py` 不动）：hot-update 仍经
  `STP_AGENT_DEDUP_SCAN_*` 源键映射下发，见 `agent_env_sync.py`
  `_AGENT_SCOPED_ENV_KEYS`；
- `.env.example`、`docs/development/environment-variables.md`、AGENTS.md
  Key env 表与 hot-update 分级说明同步为「控制面专用键 / Agent 键 / 下发源键」
  三行语义。

## Alternatives

- 一步切换为 `STP_BACKEND_*` 不回落：生产 `.env.backend` 未迁移期间 merge
  全部 503，未采用。
- 只加文档不改代码：验收要求控制面实际读键与文档一致，未采用。
- 控制面改用共享挂载路径统一两角色：Agent 与后端安装布局不同，值仍不同，
  无法消解同键冲突，未采用。

## Verification

- `test_resolve_scan_tool_prefers_backend_keys`：新键优先；
- `test_resolve_scan_tool_falls_back_to_legacy_with_warning`：回落 + WARNING；
- `test_resolve_scan_tool_none_when_unset`：未配置仍 None（503 语义不变）；
- 既有 `test_dedup_scan_merge.py` 全量通过（run_merge_sync 测试 patch
  resolve_scan_tool，不受键名影响）；ruff check 通过。

## Revisit

生产 `.env.backend` 迁移到 `STP_BACKEND_DEDUP_SCAN_*` 且确认无 WARNING 后，
删除旧键回落分支与示例注释。
