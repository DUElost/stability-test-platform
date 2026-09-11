# 心跳设备探测并发化：单设备假死不阻塞主循环（#730 / R04-F18 / R07-F15）

Status: implemented
Class: bug-fix

## Decision

根因：`HeartbeatThread._tick()` 串行对每台设备调用
`collect_device_info`（内部 3 条同步 ADB 命令：`echo test` 5s +
`dumpsys battery` 10s + `getprop` 5s）。整轮耗时 = 各设备耗时**线性累加**：
14 台设备中 2 台 ADB 假死时仅故障设备就 40s+，心跳主循环被拖过控制面
`HOST_HEARTBEAT_TIMEOUT_SECONDS`（默认 300s 前的周期停滞/超时窗口）→ 主机被
误判掉线 → 其全部运行中 Job 被打入 `UNKNOWN`。

修复：新增 `_collect_device_infos(discovered)`，用
`ThreadPoolExecutor(max_workers=min(len(devices), 8))`（issue 建议值）并发采集：

- 整轮采集耗时由**单个设备最坏探测时长**约束，而非设备数累加（14 台 × 假死
  不再线性叠加）；
- 结果按 discover 顺序返回（索引取序，不用 `zip(strict=)`——Agent 运行环境
  兼容旧 python3），心跳 payload 设备顺序稳定；
- **单设备异常隔离**：某设备采集抛意外异常时记 error 并继续（
  `adb_connected=False`），不再让一台设备的异常中断整轮心跳；
- 重连检测/`_last_adb_connected_by_serial` 仍在主线程按序更新（无并发写）。

显式不做（issue 期望 ②「心跳线程与 ADB IO 彻底解耦/快照异步更新」）：本单
①+③ 已封闭故障模式（tick 不再线性增长），解耦会改变心跳 payload 的时效语义
（上报旧快照）且属更大重构——列入 Revisit，等现场数据再决定。

## Alternatives

- **常驻线程池 vs 每 tick 建池**：选每 tick（14 设备/10s，线程创建开销可忽略；
  常驻池需要 shutdown 生命周期管理与测试清理）；
- **给 `future.result()` 加总超时**：各 ADB 调用已有 subprocess 超时（单设备
  ≤20s）兜底；再加一层会引入「后台子进程仍在跑但结果被丢弃」的泄漏形态；
- **缩短 ADB 探测超时**：改变探测语义（弱网/慢设备误判 offline），不在本单；
- **队列化/异步快照**：见 Decision「显式不做」。

## Verification

实际运行：

- 新增 `backend/agent/tests/test_heartbeat_parallel_probe.py` → **2 passed**
  （10 台设备含 2 台慢探测：最大并发 ≥2 且 `_tick` 耗时 <0.7s（串行 ≥0.8s）、
  顺序稳定；单设备抛异常不中断整轮且该设备落 error）；
- 心跳相邻回归：`test_heartbeat_thread_device_error.py` +
  `test_heartbeat_thread_adb_server_conflict.py` → 全绿；
- `backend/agent/tests` 全量 → **1549 passed**；
- `ruff check`（heartbeat_thread.py + 新测试）→ All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- **真机 ADB 假死注入验证**：需要挂死设备（USB 驱动假死/内核挂起）实测
  tick 耗时上界与心跳连续性；本机无假死设备，未执行。可验证点：注入后用
  心跳日志对比 tick 间隔（应保持 ~poll_interval，不再线性增长）。

## Revisit

- 若实测单设备最坏探测（5+10+5s）仍逼近心跳周期：先评估并行度/超时参数，
  再考虑 issue 期望 ② 的「探测快照异步化」（心跳只上报最近快照）；
- `discover_devices`（`adb devices -l`）仍串行——单次调用很快，暂无并发需求；
- 设备数 > 8 时按 wave 排队：14 台时最坏 2 wave；若 fleet 单机设备数显著增长，
  复评 `_DEVICE_PROBE_MAX_WORKERS` 上限（与 issue 建议保持一致，暂不提前调）。
