# inotifyd 源退出后的退避重连闭环（#1049 / R09-F08）

Status: implemented
Class: bug-fix

## Decision

本质问题：`InotifydSource._reader_loop` 在 EOF/异常后仅退出，其 docstring 约定
「上层（DeviceLogWatcher）自己观察 ``is_running()`` 重启本实例」——但上层没有
监督者：inotifyd 进程退出后 Job 继续运行、Watcher 仍报 probe 出的 capability，
却不再收任何文件事件（MTK Reconciler 可部分兜底，以其为唯一路径时持续漏采）。
策略里的 `inotifyd_reconnect_delay` 此前是死旋钮。

修复（把 docstring 的约定落实为代码）：

1. `DeviceLogWatcher` 在 `start()` 时（仅 inotifyd 源存在）拉起**源监督线程**：
   每 `min(1s, inotifyd_reconnect_delay)` 轮询 `source.is_running()`；发现退出 →
   warning + **指数退避**（`delay × 2^attempt`，封顶 60s）后调用同一实例的
   `start()`（InotifydSource 内部按 `poll() is None` 判断，支持重启）；重连失败
   不放弃、继续退避重试；恢复后 info 并复位退避计数；
2. 可观测：`WatcherStats` 新增 **`source_restarts`**（成功重连次数），随
   `watcher_summary` 上送控制面（`handle.stats.update(to_dict())` 透传）；
   退出/重连/失败/恢复各有 warning/exception/info 日志；
3. `stop()` 先停监督线程（Phase 0）再停 source，避免关停期竞争重连；
4. `inotifyd_reconnect_delay` 从死旋钮变为退避基数（默认 5s → 5/10/20/…/60s）。

## Alternatives

- **在 sources.py 内加退出回调（push 模型）**——放弃：`is_running()` + 上层观察
  是源码 docstring 已声明的边界（「不在本类职责：重连」）；push 回调扩大 Source
  接口面，且要在读线程退出点小心处理竞态；
- **Manager 级全局监督线程轮询所有 handle**——放弃：需要把 source/restart 能力
  从 DeviceLogWatcher 提升到 handle，扩大 Manager 职责；watcher 自监督与其
  batcher/puller 线程模型一致；
- **固定延迟重连（不指数退避）**——放弃：持续失败时固定间隔会长期高频重试；
  指数退避按 policy 基数增长、封顶 60s；
- **重连时重建新 InotifydSource 实例**——放弃：同一实例 `start()` 已支持重启
  （清 stop flag + 重新 Popen），复用订阅路径与回调，无需工厂改造。

## Verification

实际运行（worktree `/tmp/stp-1049`，2026-09-11）：

- `pytest backend/agent/tests/test_device_watcher.py -q` → **17 passed**
  （新增 4 例：退出后自动重连并计数、重连失败退避重试后成功、watcher 停止后
  监督线程不再重连、退避延迟 5/10/20/…/60 封顶）；
- `TESTING=1 ... pytest backend/agent/tests/ -q`（CI `pr-agent-tests` 等价路径）
  → **1551 passed**（含既有 `_FakePopen` 源用例，监督线程无回归）；
- `pytest tests/ -q` → **149 passed**；
- `ruff check .` → All checks passed；
- `check:quick` → **7 gates 全绿**。

未完成（pending）：

- 真机 inotifyd 掉线（adb 断连/被杀）的现场重连验证：本机无设备链路；
  EOF→重连语义已由可注入假源覆盖。

## Revisit

- 若 ADB 流（非 inotifyd 的 polling 源）也需要监督，同一模式扩展
  `_supervise_source` 的源类型判断；
- 重连风暴保护（60s 封顶仍持续失败时的告警联动）可后续按运维反馈加。
