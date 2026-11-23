# Agent Note: 修复 STP_AEE_NFS_ROOT 未挂载被旧 MOUNT_POINTS 掩盖的健康盲区 (#4077)

Status: implemented
Class: bug-fix

## Decision

1. Agent 侧 `backend/agent/startup_identity.py` 新增 `resolve_monitored_mount_points()`：
   - 当 `STP_AEE_NFS_ROOT` 未设或为空时，严格保留原有 `[p for p in MOUNT_POINTS.split(",") if p]` 解析结果（不做 trim、去重或丢弃空白项，避免改变含尾空格路径等既有兼容语义）；
   - 当 `STP_AEE_NFS_ROOT` 已配置时，在保留 `MOUNT_POINTS` 原项的基础上使用 `normalize_mount_point()`（仅去尾斜杠、保留路径中的字面空格，不再调用 `.strip()`）去重并追加 `STP_AEE_NFS_ROOT`，确保 `HeartbeatThread` 与 `capacity_reporter`（按 `all(...)` 判定 `mount_ok`）校验真实中心存储挂载并阻止普通作业认领（注：ADR-0063 VBUS 专用名额独立于普通 `effective_slots`）。
2. `backend/agent/heartbeat_thread.py`、`backend/agent/claim_loop.py` 与 `backend/agent/mount_health_probe.py` 协同确保热重载切根并发一致与门禁对齐：
   - `reload_from_settings()` 无条件先执行 `_reload_mount_points_from_env()`，不被无效心跳节奏旋钮（`#2279` 提前返回）阻塞；不仅监控列表变化时触发，在监控列表不变但权威共享根 `STP_AEE_NFS_ROOT`（`_shared_storage_root`）切换时同样递增 `_mount_points_generation`、调用 `MountHealthProbe.reset()` 并立即将普通 `effective_slots` 归零。
   - `MountHealthProbe.snapshot()` 在锁内于取样前先捕获 `sample_gen = _probe_generation` 并透传至 `_maybe_start_probe(expected_generation=sample_gen)`；`reset()` 递增 `_probe_generation` 并重置 `_active_probe_generation`，不仅丢弃已启动的旧代际 worker，也阻止取样后在 `_maybe_start_probe` 前遭遇 `reset()` 的旧路径快照领取新代际。
   - `_tick()` 通过 `_snapshot_current_mounts()` 在 `_mount_points_lock` 内同步读取 `_mount_points_generation`、`_mount_points` 及 `MountHealthProbe.current_generation()`，并在调用 `MountHealthProbe.snapshot()` 时以线程局部 `bound_generation()` 绑定该代际（不改变 `snapshot(points)` 调用签名），确保复制旧列表后、进入 `snapshot()` 前遭遇 `reset()` 时旧路径不会领取新代际或伪造 `probe_stale=False`。
   - 新增 `backend/agent/outbound_guard.py` 提供 `OutboundDispatchGate`（按代际排空的并发发包屏障）与线程局部守卫：
     - `reload_from_settings()` 在 `_reload_publish_lock` 内完成「`_mount_points_lock` 发布切根（递增代际、reset、归零普通槽位）+（若有变化）`advance_and_drain()`」；同配置二次 reload 因持有同一把发布锁，必须等首次排空结束后才能 ACK（#4077 R6，避免 `changed=False` 绕过尚未完成的屏障）；
     - `heartbeat_thread.py` `_tick()` 在重采环内先求值 `artifact_digest` 与直接调用 `compute_capacity(...)`（保持 `#2900`/`#2957`/`#3092` AST 接线守卫兼容性），并将 `bind_heartbeat_stale_check(..., dispatch_gate=self._dispatch_gate)` 绑定至真实 `heartbeat.send_heartbeat()`（在 `enter_bound_heartbeat_send()` 内检查后调用 `requests.post()`）与真实 `AgentSocketIOClient._emit()`（在 `self._lock` 内经 `enter_bound_heartbeat_send()` 检查后调用 `self._sio.emit()`）；
     - `claim_loop.py` `process_claim_tick()` 先取 `vbus_claim_slots()` 再读普通 `effective_slots`，并绑定 `bind_claim_capacity_reader(..., dispatch_gate=self._dispatch_gate)` 让真实 `api_client.fetch_pending_jobs()` 在 `enter_bound_claim_send()` 窗口内实时钳制 `body["capacity"]` 并与 ACK 排空对齐（`vbus_slots` 保持独立）。
3. 控制面 `backend/services/file_server_monitor.py` 的 `_host_mount_summary()` 抽取 `_resolve_host_mount_ok()` 与 `_normalize_mount_key()`（仅去尾斜杠、保留字面空格）：当心跳 `mount_status` 存在当前控制面权威 `shared_root`（`STP_AEE_NFS_ROOT`）键时，收集全部匹配项且仅当全部为合法布尔值 `ok=True` 才判已挂载，任意非法/缺失返回 `None`；仅在权威键缺失时回落 `any(ok_flags)`（旧版 Agent 缺键时的 `/storage` 展示与告警边界归 `#4109`）。

## Alternatives

- **仅在部署脚本手动改 `MOUNT_POINTS`**：不治本，存量主机漏改或 `reload_config` 切根后会再次静默上报 `mount_ok=True`。
- **把 `_host_mount_summary` 全局改成 `all(ok_flags)`**：会破坏早期仅挂单一路旧挂载点的兼容测试；缺键展示问题在 `#4109` 另案收口。

## Verification

- `backend/agent/tests/test_startup_identity_736.py`：覆盖 `STP_AEE_NFS_ROOT` 已配/未设/空值、含尾空格不同路径不误合并及 `bootstrap_process_identity()` 入口回归。
- `backend/agent/tests/test_mount_health_probe_b10_u1.py`：覆盖 `MountHealthProbe.reset()` 递增代际丢弃在途旧 worker 结果、阻止旧路径快照在 `reset()` 后领取新代际与即时重探。
- `backend/agent/tests/test_agent_settings_heartbeat.py`：覆盖 `reload_from_settings()` 切换未挂载新根（含监控列表不变仅切换 `STP_AEE_NFS_ROOT`）后立即归零普通 `effective_slots`、在途发包窗口阻塞至完成才返回 ACK、同配置重叠 reload 不得抢先 ACK、`send_heartbeat()` 负载构建期间/WS 等待发送锁期间切根不向外发旧根、复制旧列表后进 `snapshot()` 前 `reset()` 不领取新代际，以及非法节奏旋钮下挂载刷新照常生效。
- `backend/agent/tests/test_claim_loop_736.py`：覆盖 `process_claim_tick()` 在 `vbus_claim_slots()` 期间以及真实 `fetch_pending_jobs()` 构造 `active_job_ids` 期间发生切根归零后 `requests.post` 仅带 `capacity=0` 与独立 `vbus_slots=1`。
- `backend/agent/tests/test_agent_singleton_2961.py` / `backend/agent/tests/test_kernel_usb_faults.py`：验证恢复 `compute_capacity(...)` 直调关键字写法后两项 AST 接线守卫通过。
- `backend/tests/services/test_file_server_monitor.py`：覆盖 `_host_mount_summary` 权威键 `ok=False` / 非法布尔值、含尾空格键不遮蔽目标根及 `collect_file_server_overview()` 入口回归。

## Revisit

- **部署前提**：全队最新盘点（`#4077` Ansible 复核）确认 **13 台缺挂载 `/mnt/stp-log`、1 台（`172.21.x.x`）待复核**；须先补齐挂载、复核未知主机并在 Agent 用户下逐台验证可读写后再热更新/重启（未挂载即上线新门控会使该主机普通 `effective_slots` 压为 0，`UNSCHEDULABLE` 为健康状态字段，不等于 Host 离线）。
- 混部旧版 Agent 缺报 `/mnt/stp-log` 键时的 `/storage` 告警与 UI 标签口径见 `#4109`。
