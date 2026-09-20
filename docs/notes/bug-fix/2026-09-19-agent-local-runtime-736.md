# #736 切片：SIO + LocalDB 启动段抽出 `local_runtime`

Status: implemented
Class: bug-fix

## Decision

把启动期本地面迁到 `local_runtime.py`（叠在 `startup_guards` / #2805 之上）：

| 符号 | 职责 |
|---|---|
| `connect_socketio_with_early_control` | P2-2a 缓冲 handler + connect/reconnect |
| `initialize_local_stores` | LocalDB / DLE bind / AEE migrate / patrol / scripts |
| `replay_early_control_commands` | 真实 handler 就绪后回放队列 |

顺带把 DLE `bind_local_db` 双形态 import 升顶层，inner-imports **604 → 602**。

同 PR 棘轮：`main.py` 536 → **503**，封顶 **529**（×1.05）。

## Alternatives

- **直接上 `AgentApplication`**：弃——先搬走可测启动段，再挂薄壳；
- **只抽 SocketIO、LocalDB 留 main**：弃——同属本地面 bootstrap。

## Verification

- local_runtime + startup_guards：**15 passed**
- `check:quick`：见 PR

## Revisit

- 下一刀：薄壳 `AgentApplication`，或 heartbeat / lease / recovery 接线段。
