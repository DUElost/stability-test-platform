# monkey_launch aimwd post-check 独立窗口（#1711）

Status: implemented
Class: bug-fix

## Decision

monkey_launch v5.0.2：MonkeyTest.sh 与 aimwd（MonkeyWatchdog）post-check
各用 `time.time() + max_wait` 独立 deadline。v5.0.1 共用同一 deadline，
sh 轮询耗满窗口后 aimwd 二次 poll 无预算 → 假失败 exit 1。

种子迁移 `z1a2b3c4d5e6` 登记 v5.0.2、停用 v5.0.1。

## Alternatives

- **调大 max_wait**：掩盖根因，仍会在 sh 更慢时复发；否决。
- **原地改 v5.0.1**：违反已发布版本不可变；否决。

## Verification

- `python -m pytest backend/agent/tests/test_monkey_watchdog_chain_809.py -k launch -q`
- `python scripts/run_gates.py check:quick`
- `#1744` 解双 head：`z1a2` `down_revision` 从 `z7a6` 重链到 `f6a5`
  （#1743 合入后 main tip）；`cd backend && python -m alembic heads`
  → 唯一 head `z1a2b3c4d5e6`；链 `z7a6 → f6a5 → z1a2`。空库 upgrade
  本环境无 docker，以 CI `pr-migrate-empty-db` 为准。

## Revisit

若 monkey_check 重启分支存在同类共用 deadline，应单独开版本修复。
