# #736 切片：host 控制面抽出 `host_control_plane`

Status: implemented
Class: bug-fix

## Decision

把 heartbeat 之后的 host 全局控制面迁到 `host_control_plane.py`：

| 符号 | 职责 |
|---|---|
| `HostControlPlane` | scheduler / coordinator / occupancy / lease / register 袋 |
| `start_host_control_plane` | 启动并 late-bind 到 heartbeat + `ControlHandlerDeps` |

同 PR 棘轮：`main.py` 461 → **406**，封顶 **427**（×1.05）。

## Alternatives

- **直接上 `AgentApplication`**：弃——lease/scheduler 仍是最大接线块，先搬走；
- **连同 job-pool / recovery sync 一并抽**：弃——executor 与 resume 晚绑定更密，下刀。

## Verification

- host_control_plane：**2 passed**
- `check:quick`：见 PR

## Revisit

- 下一刀：薄壳 `AgentApplication`，或 job-pool + recovery sync /
  claim loop 外壳。
