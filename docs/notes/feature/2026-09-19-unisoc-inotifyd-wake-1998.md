# 展锐 inotifyd 实时唤醒层（#1998 P2 实时性 / ADR-0032 v1.1 D8 增补）

Status: implemented
Class: feature

## Decision

给展锐补齐与 MTK 对等的**实时信号层**（owner 方向确认：展锐日志链与 MTK 保持一致），
形态为「事件源分离、写入方唯一」的**唤醒层**，opt-in **默认关**：

- `STP_WATCHER_UNISOC_INOTIFYD=true` 且 `detect_device_platform == UNISOC` 时，
  `WatcherPolicy` 注入 `UNIVIEW: [<uniview 根>]` 分类并同步
  `required_categories=["UNIVIEW"]`（否则缺省 MTK AEE 类在展锐必败 → probe 失败 →
  watcher 恒 `unavailable` 连 `DeviceLogWatcher` 都不创建——48h 生产实测 2639 job
  100% unavailable 的根因，`policy.py` DEFAULT_PATHS 平台盲）。
- `DeviceLogWatcher` 把 UNIVIEW inotifyd 事件**只**转成 `UnisocUniviewReconciler
  .wake()`（`_route_unisoc_wake` 在 puller/emit 之前拦截）；信号与 DLE 仍由
  reconciler tick 独占产出——不出现第二写入方，幂等键/DLE 语义零改动。
- `UNIVIEW` 进 `DEFAULT_IMMEDIATE_CATEGORIES`：batch 默认 5s 聚合会吃掉秒级收益。
- reconciler 循环把 `stop_evt.wait(baseline)` 换成可被 wake 截断的
  `_wait_until_next_tick()`，带 `STP_WATCHER_UNISOC_WAKE_MIN_INTERVAL_SECONDS`
  地板（默认 2s，占位值未经真机校准）防 inotifyd 事件风暴；`wake_ticks` 入
  `ReconcilerStats`（沿 #2394「UNISOC 回填、MTK 恒 0」先例）供运维确认唤醒层在工作。

## Alternatives

- **inotifyd 直接 emit UNIVIEW 信号/DLE**：秒级可达，但与 reconciler 形成双写方
  （processed 签名语义、emit_intent 幂等键都按 tick 独占设计），去重回归面大，否决。
- **照搬 MTK 抑制语义**（UNIVIEW 进 paths、reconciler 接管期间只拉不 emit）：改动
  最小但信号延迟仍由 180s 轮询决定，不满足本项「秒级」目标，否决。
- **维持现状只等真机**：实时层对展锐恒缺席（2639/2639 unavailable），与「与 MTK
  一致」方向相悖；改以 opt-in 默认关落地仓库侧，真机探测通过后仅需下发 env 即启用。

## Verification

- 新增 `backend/agent/tests/test_unisoc_wake.py` 13 例：唤醒截断 baseline（<1s）/
  地板限频（≥0.9s）/ stop 双路即时返回 / `wake_ticks` 计数 / UNIVIEW 批量与
  immediate 双路拦截（不 emit、不进 puller）/ 回调异常隔离 / 无回调仍消费 /
  immediate 集契约 / 门控三态（关=原样、开+UNISOC=注入、开+MTK=原样）。
- 回归：`test_unisoc_reconciler.py`、`test_device_watcher*.py`、
  `test_job_session*.py`、`test_batcher.py`（immediate 集结构断言按新契约更新）、
  `test_local_db_watcher.py`、`test_bootstrap_subsystems_736.py` 合计 **128 passed**。
- 门禁：`python3 scripts/run_gates.py check:quick`（worktree 旁路：主树 .venv 解释器
  + frontend/node_modules 软链）；`check_governance_surface.py --check`。

## Revisit

- **启用判据**：#1998 真机探测清单（inotifyd 在位 / uniview 目录 shell 可读性 /
  mask 支持 / 写盘时序）通过前，本层保持默认关；探测后如需调地板，改
  `STP_WATCHER_UNISOC_WAKE_MIN_INTERVAL_SECONDS` 即可。
- **`< 5s` 端到端延迟与不重复建行**：需真机验收（#1998 P2 实时性验收项），仓库侧
  单测只覆盖机制语义。
- `_wake_min_interval` 默认值 2s 为占位；真机事件频率数据到位后再校准。
