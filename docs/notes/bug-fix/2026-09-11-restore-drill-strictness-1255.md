# 恢复演练严格化：SQL 错误与表数不足不得报 PASSED（#1255 / R14-F09）

Status: implemented
Class: bug-fix

## Decision

本质问题：`pg_restore_test.sh` 的恢复与校验是"宽松通过"，三处叠加使不完整恢复也可能
输出 `Restore drill PASSED`：

1. `gunzip | psql` 未启用 `ON_ERROR_STOP`：经 stdin 执行时部分语句失败 psql 仍可能
   返回 0，`if !` 放行（`scripts/pg_restore_test.sh:60`）；
2. 计数查询 `2>&1` 合并 stderr：查询失败的错误文本会混入计数变量（`:66-71`）；
3. `TABLE_COUNT < 5` 仅 WARNING：表数不足乃至 0 张仍继续输出 PASSED（`:78-80`）。

修复（只收紧通过语义，不改备份/恢复路径）：

- 恢复段加 `-v ON_ERROR_STOP=1`：任一 SQL 错误立即使 psql 非零 → `ERROR: Restore failed` + exit 1；
- 计数查询抽 `run_count_query`（stderr 不入变量、`-t -A`）：查询失败（管道非零）直接
  ERROR + exit 1，输出非数字再经 `check_count` 兜底；
- 表数不足由 WARNING 改为 ERROR + exit 1；
- 头部注释写明通过条件。

`Restore drill PASSED` 自此是真正的全绿信号。

## Alternatives

- **保留 WARNING、仅加 ON_ERROR_STOP**——放弃：表数不足（如 schema 恢复缺失）仍会
  PASSED，验收第 2 条不满足；
- **引入 schema 版本/约束级断言**——放弃：本单验收不需要，且需与迁移版本建立额外
  单源对齐；表级 + ON_ERROR_STOP 已覆盖"漏报"主体，见 Revisit；
- **在真库跑集成测试**——放弃：本机为生产库宿主，禁止在生产库试跑恢复
  （AGENTS.md 硬约束）；以 stub 注入覆盖脚本自身判定逻辑，真实演练留隔离环境。

## Verification

实际运行（worktree `/tmp/stp-1255`，基于 `origin/main`）：

- `pytest tests/test_pg_restore_drill.py -v` → **6 passed**：
  - 中间 SQL 错误（`STUB_RESTORE_RC=1`）→ 非零且无 PASSED；
  - 表数不足（`STUB_TABLE_COUNT=2`）→ 非零且无 PASSED；
  - 计数查询失败（`STUB_TABLE_RC=1`）→ 非零且无 PASSED；
  - 全绿路径 → exit 0 + `Restore drill PASSED`；
  - 静态：restore 行必须含 `-v ON_ERROR_STOP=1`；表数 guard 必须是 exit 1 且脚本无 WARNING；
  - stub psql 自校验恢复调用带 `ON_ERROR_STOP=1`（缺失即失败），使静态参数成为
    运行时可观察行为；
- **反向验证**：去掉 `ON_ERROR_STOP` → 2 例失败（运行时 + 静态），确认测试可捕获回归；已恢复；
- `bash -n scripts/pg_restore_test.sh` → 通过；
- `check:quick` → 7 gates 全绿（ruff / eslint / tsc / knip / compileall / gov-surface / ai-work）。

未完成（pending）：

- 真实验收第 3 条的环境侧——用真实备份在隔离 PG 上完整演练：本机为生产数据库宿主，
  不在本地生产库执行恢复；需隔离环境跑 `pg_restore_test.sh` 验证 PASSED 与 RTO。

## Revisit

- 若演练需要更细的通过条件（schema 版本、关键约束、样本行），引入与 alembic 版本
  对齐的断言；
- 若备份调度（systemd timer）落地并产出稳定备份产物，将本演练接入周期任务并记录
  RTO（ADR-0025 收口项）。
