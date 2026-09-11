# auto_archive_sweep 终态选型改为最旧到期未归档（#833）

Status: implemented
Class: bug-fix

## Decision

无 RUNNING 时，终态分支不再按 `max(id)` 取最新 PlanRun，改为：

1. 过滤 `ended_at + interval` 已到期的 SUCCESS / PARTIAL_SUCCESS；
2. 按 `ended_at ASC, id ASC` 取候选；
3. 跳过归档已完成（merge + extract，#1110）的条目，enqueue 第一条仍需归档的；
4. 仍保持「每 Plan 每轮最多 1 条」。

这样较早终态 run 不会因后续重跑而被永久饿死（SUCCESS 无报表）。

## Alternatives

- **每轮处理多个到期 run**：吞吐更高，但放大 SAQ/Agent scan 并发；本单保留单条节流。
- **仅当最新 run 已完成才回退到更旧**：仍可能在最新 run 未到期时饿死旧 run，弃用。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/agent/tests/test_saq_scan_pipeline.py -q -k auto_archive_sweep
```

## Revisit

- 大量历史未归档终态 run 会按轮次逐个消化；若积压成为运维问题，再议每轮批量上限。
- 权威描述已回写 `docs/design/06-realtime-and-background.md`。
