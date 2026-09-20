# #736 切片：heartbeat 接线抽出 `heartbeat_bindings`

Status: implemented
Class: bug-fix

## Decision

把 HeartbeatThread 构造与 capacity / reconnect 回调迁到
`heartbeat_bindings.py`：

| 符号 | 职责 |
|---|---|
| `RecoveryActionsSlot` | 晚绑定 `execute_recovery_actions`（reconnect 钩子） |
| `build_active_count_getters` | 线程安全 active job/device 计数 |
| `build_heartbeat_thread` | 组装未 start 的 HeartbeatThread |

顺带把 `read_artifact_digest` 双形态 import 与 `patrol_recovery` 升顶层，
inner-imports **602 → 599**。

同 PR 棘轮：`main.py` 503 → **461**，封顶 **485**（×1.05）。

## Alternatives

- **直接上 `AgentApplication`**：弃——heartbeat 仍是最大单块接线，先搬走；
- **连同 scheduler/coordinator 一并抽**：弃——控制面启动顺序敏感，下刀再动。

## Verification

- heartbeat_bindings + 相关 main 测：**12 passed**
- `check:quick`：见 PR

## Revisit

- 下一刀：薄壳 `AgentApplication`，或 lease / scheduler+coordinator /
  job-pool 接线段。
