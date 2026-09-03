# AEE crash detection chain (初筛选)

> 仅在改动 `backend/agent/aee/` 时加载。
> **风险评级 S/A/B 的实现在控制面** `backend/services/log_observation.py:aggregate_risk_summary`（DLE 权威 + 未链接 signal），
> 规则表见根 `AGENTS.md` §scan/upload/merge 跨进程契约 —— 本文只写 Agent 侧的采集与上报。

两层互补，Reconciler 为主、inotifyd 为兜底：

## 平台路由（#73 / ADR-0032 D6，supersede #220 单白名单）

AEE 是**联发科专有机制**，展锐/高通机型没有 `/data/aee_exp`。`JobSession` 在启动
Reconciler 前先探测 `ro.soc.manufacturer`（回退 `ro.board.platform` 前缀）归一化为
`MTK` / `UNISOC` / `QCOM` / `UNKNOWN`，再按平台路由（`job_session._resolve_reconciler_class`）：
MTK/UNKNOWN → `AeeDbHistoryReconciler`，UNISOC → `UnisocUniviewReconciler`
（`unisoc_reconciler.py`，轮询本地 `uniview_watcher` 树，ADR-0032 D8），QCOM → 跳过。
原 `STP_WATCHER_AEE_RECONCILE_PLATFORMS` 单白名单键已随路由删除。

Collector 同样按平台分发（`collector.get_collector_for_platform`）：MTK/UNKNOWN →
`MtkPlatformCollector`；UNISOC → `UnisocPlatformCollector`（**真实现**：detect 探测
`/data/uniview` + `/data/vendor/uniview`，`parse_metadata` 读 `unievent_info.json`）；
QCOM → stub（`detect→False`，`parse_metadata` 抛 `CollectorError`），有对应设备时跳过。

- 探测结果按 serial 进程内缓存，并随心跳写入 `device.platform`
- `UNKNOWN` **恒放行**（按 MTK 处理）— adb 抖动导致的探测失败不该让 MTK 机型漏采崩溃信号
- 生产实测（2026-07-26）：Z2581/Z2582 = UNISOC（`ums9230`），DAM-M500 / ELA-LX2 /
  ELA-LX3 / MLD-LX3 = MTK（`mt6768`）

## Reconciler（主路径，默认开）

每 60s 基线周期（`STP_WATCHER_AEE_RECONCILE_ENABLED=true`，默认开启）：
1. `adb shell cat /data/aee_exp/db_history` + `/data/vendor/aee_exp/db_history` → sha256 对比判断是否变化
2. 新行 → `adb pull` 整目录到 Agent HDD
3. 读 **`ZZ_INTERNAL`** 优先解析（CSV：parts[0]=exp_class, parts[7]=cur_process）
4. 读 `__exp_main.txt` fallback
5. `SignalEmitter.emit(source="reconciler")` → `extra={event_type, event_subtype, package_name, aee_ts, nfs_path}`

日志标记：`aee_reconciler_emit`（DEBUG 级，含 `pkg=` / `subtype=`）

## inotifyd（兜底路径）

Reconciler 启动失败时自动回退：
1. `adb shell inotifyd - /data/aee_exp:nwx /data/vendor/aee_exp:nwx` 实时监听
2. 文件创建/写入 → `SignalEmitter.emit(source="inotifyd")`
3. 不读 ZZ_INTERNAL，`extra` 为 NULL（仅提供计数）

日志标记：`device_log_watcher_emit_fallback`（INFO 级，表示兜底激活）
回退标记：`aee_reconciler_emit_rollback`（WARNING 级，表示 Reconciler 启动失败）

## 监测目录

- MTK：`/data/aee_exp` + `/data/vendor/aee_exp`（`/data/aee_exp` 包含 ANR 信息，`/data/anr` 不再监测）
- UNISOC：`/data/uniview` + `/data/vendor/uniview`（`UnisocPlatformCollector.detect` 探测）

## 数据流

```
ZZ_INTERNAL / __exp_main.txt → SignalEmitter → local SQLite outbox
  → POST /agent/log-signals → job_log_signal 表 (extra JSONB)
  → Frontend watcher-summary (按 category/package 聚合)
  → AnomalyDashboard (双饼图 + 包名榜) / WatcherSummaryCard (异常率进度条)
```

`count_dbg_process.py`（scan tool 目录下）独立统计工具，同样读 ZZ_INTERNAL，不与平台代码集成但解析逻辑对齐。
