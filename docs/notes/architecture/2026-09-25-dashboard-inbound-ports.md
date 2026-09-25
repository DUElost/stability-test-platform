# /dashboard 入站能力改为组装根注入，解开 realtime ↔ services import 环

Status: implemented
Class: architecture

## Decision

`backend/realtime/socketio_server.py` 被 services 约 20 个文件用作推送出口（`schedule_emit` /
`broadcast_*` / `call_agent_*`），同时它的 `DashboardNamespace` 又 import 了两个 services 实现：
`auth_session.authenticate_token`（握手 token 认证）与 `run_console.RunConsole`（console 房间
存在性），形成 realtime ↔ services 的环。

- `socketio_server` 删除对 services 的 import，改为声明两个入站端口：
  `configure_dashboard_ports(authenticate_user=..., console_run_exists=...)`；
  **未注入时 fail-closed**：token 认证一律拒绝、console 房间一律不放行；
- 新增 `backend/services/realtime_ports.py`：`authenticate_access_token`（与原
  `_authenticate_dashboard_user` 同一校验面，#903）、`console_run_exists`、`wire_dashboard_ports()`；
- 组装根 `backend/main.py` 在 `create_sio_server()` 前调用 `wire_dashboard_ports()`；
- 线程语义不变：两个回调仍经 `asyncio.to_thread` 执行（#1041 / #2056）；
- 测试：`backend/tests/realtime/conftest.py` autouse 按生产组装注入；`test_dashboard_auth` 的
  打桩目标改为 `realtime_ports`；新增未注入 fail-closed 与 main 接线两条测试；
- `.importlinter` C1 基线删除 2 行 `realtime.socketio_server → services.*`（56 → 54）。

## Alternatives

- **把推送 API 整体拆到 `realtime/emit.py`**（快照原建议）：弃——要改约 20 个 services 调用方
  与大量测试打桩目标，而环只由入站侧 2 条边造成；推送出口本来就该在 services 之下，
  需要移走的是入站侧对 services 的依赖。依赖倒置只动 2 条边。
- **把 `DashboardNamespace` 整体移到入口层**：弃——`create_sio_server` 负责注册命名空间，
  移走后注册也要跟着拆，改动面更大，收益相同。
- **未注入时回退到直接 import services**：弃——那会把环以函数内 import 的形式藏回去，
  而且测试会在「漏接线」时静默通过。

## Verification

- `lint-imports`：5 条合约 KEPT，C1 忽略项 4 → 2；
- `backend/tests/realtime` + `test_main_realtime_ports` + `test_websocket` + `test_health_saq`
  对隔离 PostgreSQL 16：148 passed；
- 根 `tests/test_realtime_wiring_contract.py` / `test_agent_import_boundary.py` /
  `test_plan_run_abort_import_contract.py`：16 passed；
- 后端全量套件结果见 PR 描述。

## Revisit

- 新增其他需要 services 能力的入站 handler 时沿用同一端口方式，不在 `socketio_server`
  里 import services（C1 会拦）。
