# Agent Note: 同步引擎连接池容量配置（#1516）

Status: implemented
Class: bug-fix
Issue: #1516

## Decision

`get_sync_engine_kwargs` 补 `pool_size` / `max_overflow` / `pool_recycle`，并把
两侧池容量抽为**同源 env 驱动**（`_pool_capacity_kwargs()`），默认值与异步池
现状一致：`30 / 60 / 1800`。

- **「同源」是本次的关键**：异步池此前把 30/60/1800 硬编码在自己函数里，同步池
  则完全没有容量参数——**两份独立事实**正是分叉的土壤。现在只有一个 env 源
  （`STP_DB_POOL_SIZE` / `STP_DB_MAX_OVERFLOW` / `STP_DB_POOL_RECYCLE`），
  改一处两侧同步。
- **默认值取 30/60/1800（= 异步池现值）**，而不是审查建议的下限 20/40：异步侧
  零行为变化，同步侧从 `5+10` 提到 `30+60`，且不必引入新的业务裁定值。
- **非法/非正值回退默认**（`abc` / 空串 / `0` / 负数）：容量误配比回退默认更危险，
  `pool_size=0` 会让每次借连接直接抛 `QueuePool limit ... reached`。

为什么同步侧必须补：共享同步池的是 12 个 APScheduler 周期任务、SAQ 默认并发 10，
以及 84 处 `SessionLocal()` 调用点；触顶后经 `leader_election` 的 fail-closed
设计会连锁跳过**全部** singleton 调度（Recycler / Reconciler / Watchdog），作业
卡在 RUNNING/UNKNOWN 且无自愈出口。异步池与同步池不共享连接，异步侧的 30+60
救不了同步侧。

## Alternatives

- **只在同步侧硬编码 30/60/1800（不动异步）**：否决。改动更小，却把分叉固化——
  异步一处硬编码、同步另一处硬编码，下次调整仍会只改一侧（正是本 issue 的成因）。
- **引入独立的同步池 env（如 `STP_DB_SYNC_POOL_SIZE`）**：否决。两侧消费方不同
  （异步给 API、同步给调度/worker），但容量语义相同；两套旋钮会复现同一类漂移。
- **默认值改取审查建议的 20/40**：不采纳（暂）。20/40 满足建议下限，但会同时
  改变异步池现值，属无依据的行为变更；待按生产实测校准后再调 env，而非改默认。
- **同时补「同步池触顶时调度链行为」的行为级回归**：本次不做。构造真实连接耗尽 +
  fail-closed 链属集成面；本单只锁**配置契约**（见 Verification），行为级测试
  记入 Revisit。

## Verification

- `python -m pytest backend/tests/test_database_config.py -q`：新增 4 例（同步池容量
  齐全 / sqlite 不设 QueuePool 参数 / 两侧同源随 env 联动 / 非法值回退默认）；
  既有断言未改仍绿（异步 30/60/1800 逐键相等）。
- `python scripts/run_gates.py check:quick`（ruff / eslint / tsc / knip / compileall /
  gov-surface / ai-work）。
- **未验证（诚实标注）**：生产 `pg_stat_activity` 采样与 `QueuePool limit` 频次。
  issue 备注的两个业务前提（是否单副本 / 历史上是否已触顶）属**严重度校准**，
  不影响本修复的正确性；在数据缺失下不宣称容量已校准。

## Revisit

- 需要按生产实测校准容量时（`pg_stat_activity` + `QueuePool limit` 频次），走 env
  调整，不改默认值。
- 若出现「同步池触顶 → 调度链停摆」的真实事件，或需要防回归，补行为级集成测试
  （构造连接耗尽，断言 fail-closed 链的可观测性与自愈出口）。
- 三个新键已同步到 `docs/development/environment-variables.md`；若后续 env 清单接入
  机器校验（#737 方向），需确保其在清单内。
