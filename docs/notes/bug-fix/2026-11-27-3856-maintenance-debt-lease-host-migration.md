# 心跳允许 MAINTENANCE 欠账租约随物理挪位迁移 host_id（#3856）

Status: implemented
Class: bug-fix

## Decision

Refs #3856（现场：ACTIVE 归还欠账租约钉死旧 `host_id`，物理挪机后心跳无条件
`device_host_reassignment_blocked`，设备在新 host 失踪）与 #4185（ADR-0066
Proposed：`usb_net` 未确认归还欠账设备换机后上线与自动清偿的前置清雷）。

在 `backend/api/routes/heartbeat.py` 把 ACTIVE 租约查询从
`(device_id, host_id)` 元组提升为完整 `DeviceLease`，按租约类型分流跨主机心跳：

1. **`JOB` / `SCRIPT`**：继续阻断 `device.host_id` 与 `lease.host_id` 改绑（原
   `device_host_reassignment_blocked`），防止运行中作业被抢走宿主。
2. **归还欠账 `MAINTENANCE`**（复用 `device_return.is_debt_lease`；非占位
   serial）：同事务更新 `device.host_id`（及 status/adb/遥测/`last_seen`）并迁移
   `lease.host_id = reported_host`，记 `WARNING maintenance_debt_lease_host_migrated`；
   **不释放**租约，不改 `fencing_token` / `device_return_debt`。有活跃租约时
   status 仍为 `BUSY`。新 host 的 `open_debts_for_host_sync` 可立即发现欠账。
3. **非欠账 `MAINTENANCE`**（人工维护 / 预留 `manual_network_supply`）：允许更新
   `device.host_id` 等可见性字段并保持 `BUSY`，**不改** `lease.host_id`、不释放；
   记 `WARNING maintenance_lease_host_mismatch`。
4. **占位 serial 的 `MAINTENANCE`**：继续阻断，避免多主机乒乓迁移租约。

不新增 Alembic 迁移，不改 `DeviceLease` 唯一索引，不改 `device_return.py`
清偿/放行语义。

## Alternatives

- **仅更新 `device.host_id`、租约钉在旧 host**（issue 早期方案 A）：弃——
  `open_debts_for_host_sync` / ADR-0061 自动清偿按
  `DeviceLease.host_id == Device.host_id` 关联；只改 device 会使新 host 看不到
  欠账载荷，阻断 #4185 清偿路径。
- **新增 `last_reported_host` 列并在 UI 标注**（方案 B）：弃——本单是结构清雷，
  读侧展示可后置；且不解决欠账随 host 迁移。
- **跨主机心跳直接释放欠账租约**：弃——派发器必须继续按
  `device_return_debt` / `active_lease` 阻断，直至清偿或人工放行。

## Verification

- `.venv/bin/python scripts/run_pytest.py backend/tests/api/test_heartbeat.py::TestHeartbeatMaintenanceLeaseHostReassignment3856 -q` → 6 passed
- `.venv/bin/python scripts/run_pytest.py backend/tests/api/test_heartbeat.py::TestHeartbeatHostReassignment -q` → 3 passed
- `.venv/bin/python scripts/run_gates.py check:quick`（见 PR 实测）

## Revisit

- ADR-0066 Phase 1 落地 `manual_network_supply` 时，复核非欠账 MAINTENANCE 分支的
  日志与派发语义是否仍满足「设备可见、不可派发、租约钉原 host」。
- 若生产再出现占位 serial 欠账挪机，应走 #1356/#3513 真 serial 治理，而不是放宽
  占位租约迁移。
