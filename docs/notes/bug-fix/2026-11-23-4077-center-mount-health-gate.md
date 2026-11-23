# Agent Note: 修复 STP_AEE_NFS_ROOT 未挂载被旧 MOUNT_POINTS 掩盖的健康盲区 (#4077)

Status: implemented
Class: bug-fix

## Decision

1. Agent 侧 `backend/agent/startup_identity.py` 新增 `resolve_monitored_mount_points()`：
   - 当 `STP_AEE_NFS_ROOT` 未设或为空时，严格保留原有 `[p for p in MOUNT_POINTS.split(",") if p]` 解析结果（不做 trim、去重或丢弃空白项，避免改变含尾空格路径等既有兼容语义）；
   - 当 `STP_AEE_NFS_ROOT` 已配置时，在保留 `MOUNT_POINTS` 原项的基础上使用 `normalize_mount_point()`（仅去尾斜杠、保留路径中的字面空格，不再调用 `.strip()`）去重并追加 `STP_AEE_NFS_ROOT`，确保 `HeartbeatThread` 与 `capacity_reporter`（按 `all(...)` 判定 `mount_ok`）校验真实中心存储挂载并阻止普通作业认领（注：ADR-0063 VBUS 专用名额独立于普通 `effective_slots`）。
2. `backend/agent/heartbeat_thread.py` 与 `backend/agent/mount_health_probe.py` 协同确保热重载切根并发一致：
   - `reload_from_settings()` 无条件先执行 `_reload_mount_points_from_env()`，不被无效心跳节奏旋钮（`#2279` 提前返回）阻塞；切根时在 `_mount_points_lock` 下递增 `_mount_points_generation`、调用 `MountHealthProbe.reset()` 并立即将普通 `effective_slots` 归零（防止 ACK 当刻在途认领继续按旧容量发单）。
   - `MountHealthProbe.reset()` 递增 `_probe_generation` 并重置 `_active_probe_generation`，使切根前已启动的旧后台探测线程完成后自动丢弃结果与节流戳，不得伪造新根 `probe_stale=False`。
   - `_tick()` 在提交 `_effective_slots`、HTTP `send_heartbeat` 与 WS 推送前校验 `_mount_points_generation`，若期间发生切根则按新根重采或放弃过时提交。
3. 控制面 `backend/services/file_server_monitor.py` 的 `_host_mount_summary()` 抽取 `_resolve_host_mount_ok()` 与 `_normalize_mount_key()`（仅去尾斜杠、保留字面空格）：当心跳 `mount_status` 存在当前控制面权威 `shared_root`（`STP_AEE_NFS_ROOT`）键时，收集全部匹配项且仅当全部为合法布尔值 `ok=True` 才判已挂载，任意非法/缺失返回 `None`；仅在权威键缺失时回落 `any(ok_flags)`（旧版 Agent 缺键时的 `/storage` 展示与告警边界归 `#4109`）。

## Alternatives

- **仅在部署脚本手动改 `MOUNT_POINTS`**：不治本，存量主机漏改或 `reload_config` 切根后会再次静默上报 `mount_ok=True`。
- **把 `_host_mount_summary` 全局改成 `all(ok_flags)`**：会破坏早期仅挂单一路旧挂载点的兼容测试；缺键展示问题在 `#4109` 另案收口。

## Verification

- `backend/agent/tests/test_startup_identity_736.py`：覆盖 `STP_AEE_NFS_ROOT` 已配/未设/空值、含尾空格不同路径不误合并及 `bootstrap_process_identity()` 入口回归。
- `backend/agent/tests/test_mount_health_probe_b10_u1.py`：覆盖 `MountHealthProbe.reset()` 递增代际丢弃在途旧 worker 结果与即时重探。
- `backend/agent/tests/test_agent_settings_heartbeat.py`：覆盖 `reload_from_settings()` 切换未挂载新根后立即归零普通 `effective_slots`、阻塞在途旧 tick 不回写旧槽位/不上报旧根，以及非法节奏旋钮下挂载刷新照常生效。
- `backend/tests/services/test_file_server_monitor.py`：覆盖 `_host_mount_summary` 权威键 `ok=False` / 非法布尔值、含尾空格键不遮蔽目标根及 `collect_file_server_overview()` 入口回归。

## Revisit

- **部署前提**：全队最新盘点（`#4077` Ansible 复核）确认 **13 台缺挂载 `/mnt/stp-log`、1 台（`172.21.x.x`）待复核**；须先补齐挂载、复核未知主机并在 Agent 用户下逐台验证可读写后再热更新/重启（未挂载即上线新门控会使该主机普通 `effective_slots` 压为 0，`UNSCHEDULABLE` 为健康状态字段，不等于 Host 离线）。
- 混部旧版 Agent 缺报 `/mnt/stp-log` 键时的 `/storage` 告警与 UI 标签口径见 `#4109`。
