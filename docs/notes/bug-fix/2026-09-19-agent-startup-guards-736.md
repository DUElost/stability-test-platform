# #736 切片：启动门禁抽出 `startup_guards`

Status: implemented
Class: bug-fix

## Decision

把启动期门禁迁到 `startup_guards.py`：

| 符号 | 职责 |
|---|---|
| `check_agent_version` / `version_lt` | 过旧 Agent 拒启 |
| `ensure_adb_server_on_startup` | 与 `reload_config` 共用的 ADB 收敛 |
| `migrate_legacy_aee_state_on_startup` | AEE 命名空间迁移日志包装 |

`main` 只接线。顺带把 `check_agent_version` 内 2 处局部 import 升顶层，
inner-imports **606 → 604**。

同 PR 棘轮：`main.py` 630 → **536**，封顶 **563**（×1.05）。

## Alternatives

- **直接上 `AgentApplication`**：弃——先搬走独立可测门禁，再挂薄壳；
- **只抽 version、ADB 留 main**：弃——同属启动闸，一并搬家减少来回。

## Verification

- startup_guards + AdbServerStartupReconcile：**12 passed**
- `check:quick`：见 PR

## Revisit

- 下一刀：薄壳 `AgentApplication`（initialize / start_background /
  register_handlers / run_loop / shutdown），或 local_db / SIO early-control
  启动段。
