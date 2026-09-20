# #736 切片：job 运行时抽出 `job_runtime`

Status: implemented
Class: bug-fix

## Decision

把 host 控制面之后的 job-pool / recovery 接线迁到 `job_runtime.py`：

| 符号 | 职责 |
|---|---|
| `JobRuntime` | outbox / executor / JobRunnerState / step-trace / recovery sync 袋 |
| `start_job_runtime` | 启动并 late-bind recovery_actions + resume + control_deps |

同 PR 棘轮：`main.py` 406 → **320**，封顶 **336**（×1.05）。叠在 #2855
（`host_control_plane`）之上。

## Alternatives

- **并入 `host_control_plane`**：弃——executor/resume 依赖 plane 产物，垂直切片更清晰；
- **直接上 `AgentApplication`**：弃——claim loop + shutdown 仍在 main，下一刀。

## Verification

- job_runtime：**2 passed**（另 host_control_plane 2 passed）
- `check:quick`：见 PR

## Revisit

- 下一刀：claim loop 外壳或薄壳 `AgentApplication`。
