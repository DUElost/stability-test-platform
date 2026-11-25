# 阶段 B1：库存四个 GET 纯只读化与 session_watchdog 物理设备级联接管（#3325 / #4117）

Status: implemented
Class: bug-fix

## Decision

按 `docs/design/2026-11-24-list-pagination-and-facets-contract.md`（v1.0）§2 与 §6.2 要求，在同一个发布单元内完成后台对账接管与读路径纯只读化，不留「无写入者」空窗：

1. **统一有效状态谓词（`backend/services/inventory_status.py`）**：抽离 `host_heartbeat_expired_condition`、`host_effective_offline_condition`、`host_effective_status_expr`、`device_effective_status_expr` 及同口径纯 Python 投影 `effective_host_status` / `effective_device_status`。缺失或过期心跳判离线；新鲜 `DEGRADED` 不判离线；无主设备（`host_id IS NULL`）不受主机级联影响。
2. **后台对账完整接管（`backend/tasks/session_watchdog.py`）**：扩展 `_reconcile_host_heartbeat_timeouts` 覆盖非 `OFFLINE` 且心跳缺失/过期的主机（含 `ONLINE`、`DEGRADED`）以及存量已 `OFFLINE` 但仍有非 `OFFLINE` 关联设备的主机；落库前按 `Host.id` 升序 `FOR UPDATE` 锁行复读并复验心跳（防心跳恢复竞态），仅更新 `Device.status = 'OFFLINE'`，保留既有 Job 状态机、租约、最后观测 ADB 字段与退役事实；保留 `_check_host_heartbeat_timeouts` 2-tuple 兼容接口。
3. **四个库存 GET 纯只读与零 N+1（`backend/api/routes/devices.py`、`backend/api/routes/hosts.py`、`backend/services/device_lifecycle.py`、`backend/services/vbus_usb_presence.py`）**：移除 `_ensure_host_online_for_device`、`_ensure_host_status_up_to_date` 及全部 GET 内 `db.commit()`；`GET /api/v1/devices` 统一 `outerjoin(Host)` + `contains_eager(Device.host)`、`GET /api/v1/devices/{id}` 使用 `joinedload(Device.host)`，并在同一请求时间基准下将有效状态谓词贯穿筛选、`total` 计数、陈旧度判定（保留 VBUS 关机在槽例外）与响应序列化。

## Alternatives

- **仅移除 GET 写入不扩 watchdog**：存量离线主机、`NULL` 心跳主机及过期 `DEGRADED` 主机下的设备将长期停留在账面 `ONLINE`/`BUSY`，违反契约 §2.1；故必须在同一发布单元同步接管。
- **仅在序列化层派生状态而不改 SQL 过滤**：会导致「记录返回 `OFFLINE`，但 `?status=OFFLINE` 查不到、`?status=ONLINE` 反而查出」的口径错位；故必须使用同源 SQL 有效状态表达式。

## Verification

- `scripts/run_pytest.py backend/tests/tasks/test_session_watchdog.py backend/tests/api/test_inventory_readonly_b1_3325.py backend/tests/api/test_devices.py backend/tests/api/test_hosts.py backend/tests/api/test_vbus_usb_presence_3918.py backend/tests/core/test_job_timeout_config.py -q`
- `scripts/run_gates.py check:quick`

## Revisit

- 本 PR 只认领 `#3325` 库存读写路径，不代替 `/results/summary` 无界聚合治理；后续 B2 切片（设备/主机 Facets 与分页）将直接复用 `backend/services/inventory_status.py` 的 SQL 有效状态表达式。
