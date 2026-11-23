# Agent Note: 修复 STP_AEE_NFS_ROOT 未挂载被旧 MOUNT_POINTS 掩盖的健康盲区 (#4077)

## Decision

1. Agent 侧 `backend/agent/startup_identity.py` 新增 `resolve_monitored_mount_points()`，在保留 `MOUNT_POINTS` 环境变量的基础上自动并入去重后的 `STP_AEE_NFS_ROOT`（通过 `resolve_shared_storage_root()`）。当中心存储由 `/mnt/stp-aee` 切换到 `/mnt/stp-log` 但宿主 `.env` 尚未同步修改 `MOUNT_POINTS` 时，Agent 心跳与 `capacity_reporter`（按 `all(...)` 判定 `mount_ok`）仍会直接校验真实中心存储挂载状态并阻止继续接新单。
2. 控制面 `backend/services/file_server_monitor.py` 的 `_host_mount_summary()` 抽取 `_resolve_host_mount_ok()`：当心跳 `mount_status` 显式上报当前控制面权威 `shared_root`（`STP_AEE_NFS_ROOT`）时优先以该键的 `ok` 为准，防止旧挂载点 `ok=True` 掩盖新中心存储未挂载；若权威键尚未上报，兼容回落到「任一上报项 `ok=True`」。

## Alternatives

- **仅在部署脚本手动改 `MOUNT_POINTS`**：不治本，存量主机漏改或配置回滚后会再次静默上报 `mount_ok=True`。
- **把 `_host_mount_summary` 全局改成 `all(ok_flags)`**：会破坏早期仅挂单一路旧挂载点并保留兼容测试的既有行为，且对未迁移老机不够渐进。

## Verification

- `backend/agent/tests/test_startup_identity_736.py` 新增 `test_resolve_monitored_mount_points_includes_shared_storage_root`。
- `backend/tests/services/test_file_server_monitor.py` 新增 `test_host_mount_summary_prefers_authoritative_shared_root_over_stale_entry`。
- 运行相关测试与 `python scripts/run_gates.py check:quick`。

## Revisit

- 待阶段 A 12 台确认缺挂载主机补齐 `/mnt/stp-log`、Agent 热更新上线后，核对 `/storage` 页面与 `/api/v1/hosts` `health.mount_ok` 全量告警收口情况。
- 长期所有主机 `MOUNT_POINTS` 与 `STP_AEE_NFS_ROOT` 收口一致后，可清理旧 `/mnt/stp-aee` 兼容键。
