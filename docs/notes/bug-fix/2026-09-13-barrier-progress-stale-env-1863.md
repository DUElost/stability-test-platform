# #1863 模块级 float(env) 护栏：STP_BARRIER_PROGRESS_STALE_SECONDS

Status: implemented
Class: bug-fix

## Decision

`pipeline_engine._PEER_PROGRESS_STALE_SECONDS` 由裸 `float(os.getenv(...))`
改为 `_parse_peer_progress_stale_seconds`：try/except + `math.isfinite` +
非正回落 `120.0` 并 warning——与 #1710（`aacc44b1` / spill catchup）同口径，
堵住运维误配导致 Agent import 即崩。

对「秒」阈值额外把 `<=0` 视为非法（#1710 spill 间隔允许负有限值；此处负秒无语义）。

## Alternatives

- 只推迟到首次 barrier 使用时解析：模块常量仍可能被其它路径在 import 后立刻读；
  模块级守卫更贴「import 即安全」。
- 非法值 raise：与 puller/`_pool_env_*` / #1710 口径不一致。

## Verification

- `python -m pytest backend/agent/tests/test_pipeline_engine_barrier_stale_env_1863.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

`git grep 'float(os.getenv' backend/agent`——本单收 pipeline_engine 模块级唯一残留；
若再出现同类，优先抽共享 `_parse_finite_float_env`。
