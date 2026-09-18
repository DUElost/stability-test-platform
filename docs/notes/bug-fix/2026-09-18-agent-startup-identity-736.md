# #736 切片：身份 / HOST_ID 抽出 `startup_identity`

Status: implemented
Class: bug-fix

## Decision

把 `main()` 启动前段（身份日志 + HOST_ID 解析/自动注册 + poll/adb/secret 环境旋钮）
迁到 `backend/agent/startup_identity.bootstrap_process_identity`，返回不可变
`AgentProcessIdentity`。`resolve_or_register_host_id` 单独可测；注册 Settings
仍只在非法 HOST_ID / 自动注册分支读取（惰性契约随文件迁到本模块）。

同 PR 棘轮：`main.py` 1087 → **1015**，封顶 **1066**（×1.05）；
`check_inner_imports` 基线 610 → **607**（身份局部 import 随迁出）。

## Alternatives

- **先抽 `_handle_control`**：弃——闭包晚绑定 `coordinator` /
  `heartbeat_thread`，搬家成本更高；身份段边界更干净。
- **连同 `ensure_dirs` / SocketIO connect**：弃——目录与传输层不属于身份域。

## Verification

- `pytest` `test_startup_identity_736` + `test_agent_settings_heartbeat` →
  **53 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：`_handle_control`（依赖注入 / deps 袋）或薄壳 `AgentApplication`。
