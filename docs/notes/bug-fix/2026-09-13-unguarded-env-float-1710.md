# #1710 未护栏 env float：import 崩溃与 recovery nan 忙循环

Status: implemented
Class: bug-fix

## Decision

两处新增的裸 `float(os.getenv(...))` 与同窗口「非法值回落默认」口径对齐：

1. `local_disk_monitor._parse_spill_catchup_interval`：类体 `_SPILL_CATCHUP_INTERVAL`
   经 try/except + `math.isfinite` 解析；非法/`nan`/`inf` 回落 `30.0` 并 warning——
   避免 import 期 ValueError 拖垮 Agent 启动。
2. `main._coerce_recovery_interval`：解析 `STP_RECOVERY_SYNC_INTERVAL_SECONDS`，
   非法/非有限回落 `60.0`，再夹下限 `5`——堵住 `float("nan")` → `Event.wait(nan)`
   满速空转。

## Alternatives

- 只推迟到 `__init__` 解析 spill 间隔：仍可能在首次配置前被其它路径读类属性；
  模块级守卫更贴「import 即安全」。
- 非法值直接 raise：运维 typo 变成启动硬失败，与 puller/`_pool_env_*` 口径不一致。

## Verification

- `python -m pytest backend/agent/tests/test_local_disk_monitor.py -k spill_catchup_interval -q`
- `python -m pytest backend/agent/tests/test_main_lifecycle_784.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

同文件仍有其它 `float(os.getenv(...))`（如 archive/poll 间隔）；本单只收 #1710 点名的
两处新实例，其余另单批量护栏。
