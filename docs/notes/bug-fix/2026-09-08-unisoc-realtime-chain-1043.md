# UNISOC 实时采集链：探测降级启动 + 生产者 + UNIVIEW 契约（#1043）

Status: implemented
Class: bug-fix

## Decision

同时打通三条独立断点（仅白名单不够）：

1. **契约**：`validate_log_signal` 允许 `category=UNIVIEW`。
2. **生产者**：`UnisocUniviewReconciler.tick_once` 先从
   `/data/uniview` 与 `/data/vendor/uniview` adb list+pull 到
   `uniview_watcher/{stamp}/{serial}/`，再 emit/DLE；状态改用
   `get_state`/`set_state`。
3. **门禁**：`JobSession._maybe_start_aee_reconciler` 在
   `capability=unavailable/skipped` 时对 **UNISOC** 仍启动；无
   `DeviceLogWatcher` 时构造独立 `SignalEmitter`。MTK 保持原门禁。

## Alternatives

- 平台感知 CapabilityProber（UNISOC required=uniview）：更干净但改
  manager/sources 面更大，本单用降级启动。
- 共用 UnisocScanRunner 树：违反 ADR-0032 D8（archive vs watcher 分树）。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest \
  backend/agent/tests/test_unisoc_reconciler.py \
  backend/agent/tests/test_job_session.py \
  backend/agent/tests/test_emitter.py \
  backend/agent/tests/test_platform_collector.py -q
```

## Revisit

- 完整 #806（rollback source / suppress-bit）未纳入。
- `aee_ts` 仍暂用 subtype；与 MTK 时间字段对齐另开。
