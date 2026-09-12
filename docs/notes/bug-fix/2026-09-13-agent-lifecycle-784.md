# Agent main 生命周期批（#784）

Status: implemented
Class: bug-fix

## Decision

1. **shutdown stop 作用域**：`LogArchiver` / `LocalDiskMonitor` /
   `EventUploader` 在 watcher 门控外启动，停机时必须同作用域 stop；不得
   包进 `log_signal_drainer is not None`。`ArtifactUploader` 仍随 watcher
   分支停。补 `EventUploader.stop()`。

2. **关停排队 → FAILED**：`SchedulerShutdown` → `_canceled` →
   `job_aborted` → MQ `ABORTED` 已由 **#1012 / R07-F12** 收口；本单确认
   不回归，不改 pipeline 映射。

3. **recovery sync 周期兜底**：启动一次 + 设备重连之外，增加
   `STP_RECOVERY_SYNC_INTERVAL_SECONDS`（默认 60s）daemon 线程，对齐终态
   outbox 周期兜底；关停时 set/join。

4. **EventUploader 退避 Timer**：`daemon=True` + `stop()` cancel 未决
   Timer，避免非 daemon Timer 拖长热更/停机窗口（issue 评论补充）。

## Alternatives

- 关停时 `cancel_futures=True` 丢弃线程池排队：可能误杀已持有资源的
  future；现有 abort + SchedulerShutdown→ABORTED 足够；否决。
- recovery 挂到 heartbeat：耦合心跳失败路径；独立线程更清晰。

## Verification

- `pytest backend/agent/tests/test_event_uploader.py -q`
- `python3 scripts/run_gates.py check:quick`

## Revisit

若周期 recovery 在大 fleet 上造成控制面压力，可升默认间隔或加
jitter / host 分片。
