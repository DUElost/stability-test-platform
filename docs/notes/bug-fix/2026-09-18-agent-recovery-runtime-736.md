# #736 切片：recovery 接线抽出 `recovery_runtime`

Status: implemented
Class: bug-fix

## Decision

把 `main()` 内 recovery cancel / execute / resume / 周期线程迁到
`backend/agent/recovery_runtime.py`。`ResumeJobSlot` + 既有
`JobRunnerStateSlot` 保留晚绑定顺序（patrol/execute 先于 resume 赋值）。

同 PR 棘轮：`main.py` 825 → **792**，封顶 **832**（×1.05）。叠在
`active_job_bindings`（#2730）之上。

## Alternatives

- **等 #2730 合入再开独立 PR**：弃——用户要求继续；叠分支可并行推进。
- **连同 claim 主循环一起抽**：弃——claim 仍是最大过程块，下一刀再谈。

## Verification

- recovery_runtime + lifecycle_784 + active_job_bindings + source-scan ratchet：
  **21 passed**
- `check:quick`（除本机 `schema-at-head`）：pending

## Revisit

- 下一刀：薄壳 `AgentApplication`，或 claim 主循环垂直抽出。
