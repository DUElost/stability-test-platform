# #736 切片：SocketIO control 抽出 `control_handler`

Status: implemented
Class: bug-fix

## Decision

把 `main()` 内 142 行 `_handle_control` 与 `_parse_abort_job_ids` /
`_reload_runtime_env` 迁到 `backend/agent/control_handler.py`。晚绑定对象
（`coordinator` / `heartbeat_thread` / `operation_scheduler` /
`job_runner_state`）经可变 `ControlHandlerDeps` 袋注入，保留原启动顺序语义。

静态契约测与 `backend/agent/AGENTS.md` 的 reload_config 指针改盯新模块。

同 PR 棘轮：`main.py` 1015 → **846**，封顶 **889**（×1.05）。

## Alternatives

- **先上 `AgentApplication`**：弃——control 闭包仍是最大单块，先搬走再谈容器。
- **在 `main` 保留 re-export**：弃——与既有切片纪律一致。

## Verification

- 相关 agent 测（control_handler / misc_805 / settings_lease / settings_heartbeat /
  step5b / control_ack）：**102 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：薄壳 `AgentApplication`（挂 initialize / start_background /
  register_handlers / run_loop / shutdown），或 claim/recovery 闭包簇。
