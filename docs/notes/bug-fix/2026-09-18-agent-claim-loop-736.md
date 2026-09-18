# #736 切片：claim tick 抽出 `claim_loop`

Status: implemented
Class: bug-fix

## Decision

把 `main()` claim/poll 循环体迁到 `process_claim_tick`；`main` 只保留
`while` / signal / `finally` 停机壳。submit 失败路径的
`_arrive_patrol_barrier_preengine` 改为 `claim_loop` 顶层导入（顺带下调
inner-imports 基线 607→**606**）。

同 PR 棘轮：`main.py` 792 → **682**，封顶 **717**（×1.05）。叠在
`recovery_runtime`（#2739）之上。

## Alternatives

- **连同 while/shutdown 做成 `run_agent_loop`**：弃——停机顺序仍在演进，先
  抽 tick 更安全。
- **先上 `AgentApplication`**：弃——claim 是最后大块过程代码，搬走后再薄壳。

## Verification

- claim_loop + source-scan ratchet：**5 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：薄壳 `AgentApplication`（initialize / start_background /
  register_handlers / run_loop / shutdown），或再抽 `_check_agent_version` /
  local_db 启动段。
