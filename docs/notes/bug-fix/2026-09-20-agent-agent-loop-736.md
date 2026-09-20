# #736 切片：claim loop 外壳抽出 `agent_loop`

Status: implemented
Class: bug-fix

## Decision

把 signal / while / finally 外壳迁到 `agent_loop.py`：

| 符号 | 职责 |
|---|---|
| `run_agent_loop` | SIGTERM/SIGINT → `process_claim_tick` 循环 → `shutdown_agent_runtime` |

入参吃 `HostControlPlane` + `JobRuntime` 袋，避免 main 再拆袋。同 PR 棘轮：
`main.py` 320 → **269**，封顶 **283**（×1.05）。叠在 #2859（`job_runtime`）之上。

## Alternatives

- **直接上 `AgentApplication`**：弃——启动编排（identity→stores→planes）仍在
  `main`，下一刀再收；
- **并入 `claim_loop`**：弃——tick 与外壳职责不同，保持单迭代纯函数。

## Verification

- agent_loop：**2 passed**（另 job_runtime 2 passed）
- `check:quick`：见 PR（本地 schema-at-head 因 alembic behind 跳过）

## Revisit

- 下一刀：薄壳 `AgentApplication` 收启动编排，或把模块级 active-job 全局迁入
  plane 构造。
