# #736 切片：`AgentApplication` 生命周期阶段方法

Status: implemented
Class: bug-fix

## Decision

在已有薄壳上补 #736 验收要求的阶段划分（不重做垂直抽取）：

| 方法 | 职责 |
|---|---|
| `initialize` | identity / stores / control handler **构建** |
| `start_background_tasks` | version gate / heartbeat / host_control_plane |
| `register_handlers` | SIO control + early replay（P2-2a，须在 plane 之后） |
| `start_job_plane` | job_runtime（须在 handlers 之后） |
| `run_loop` | claim loop；`graceful_shutdown` = `run_agent_loop.finally` →
  `shutdown_agent_runtime` |

`graceful_shutdown` 不做成可独立调用的方法——停机与 stop event 同事务，拆出会误导。

## Alternatives

- **一次把 job_runtime 并进 `start_background_tasks`**：弃——handlers 必须夹在
  plane 与 job_runtime 之间；
- **`graceful_shutdown` 可调用包装**：弃——与 claim-loop 停机事件耦合，假 API。

## Verification

- 阶段方法存在 + 行数 ≤100：见 pytest
- `app.run()` 接线回归：见既有 agent_application 用例

## Revisit

- 叠 PR 全绿后可关 #736（plan_runs 侧已由 #1520 / CEILINGS 闭环；agent 侧本栈完成）。
