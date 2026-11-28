# B13-U2：统一网络供给归还欠账、迟到确认与专用审计放行闭环

Status: implemented
Class: feature

## Decision

按批次 B13（`#4212` v1.3 §3.2 U2）与 ADR-0066 D2.6、D3.4、D4.4–D4.5 落地控制面与共享归还契约扩展（无新 Alembic 迁移）：

1. **`usb_net` 唯一注册到 ADR-0061 `ONBOARDED` 族表**（`backend/agent/contracts/device_return.py`）：
   - 登记 `FAMILY_STEPS["usb_net"]`（`setup_network` / `teardown_network`）与 `ONBOARDED["usb_net"]`（`disarm_script="teardown_network"`、`disarm_min_version="1.0.0"`）；
   - 在 U4 发布 `teardown_network@1.0.0` 与主机 opt-in 能力前设置 `init_check_enabled: False`（`backend/agent/device_return.py::run_init_checks` 跳过未启用 init_check 的族），确保既有 Agent `init_check` 路径零回归；
   - 在 `probe_result_policy` 增加 `family == "usb_net"` 双侧回读判据（`device_usb_state == "adb"` 且 `host_bridge_detached is True` 且 `readback == "clear"` 且 `kind == "ok"`），无设备 APK 依赖且缺任一侧即 fail-closed 拒绝 `confirmed`。
2. **独立无环服务模块 `backend/services/network_return.py` + `device_return.py` 最小钩子**：
   - `handle_job_lease_release[_sync]` 在同一 `Device FOR UPDATE` 事务内根据 `disarm_results` 裁定 `device_network_session`（`wifi` 与 `usb_net=confirmed` 核销为 `RELEASED(teardown_confirmed)`；`usb_net` 未确认置 `RELEASE_FAILED` 并写开放欠账），再按全设备所有专项余债决定释租或原地转欠账租约（`N → N+1`），避免 `usb_net` 已清但其他专项仍欠账时提前释租；
   - `apply_discharge_sync`（校验 `N+1` 欠账令牌）与 `apply_late_confirmed[_sync]`（校验原 Job 令牌 `N`）在关闭 `usb_net` 欠账的同一事务内同步将对应未释放 `device_network_session` 置 `RELEASED(debt_discharged)` / `RELEASED(late_confirmed)`；`try_apply_late_confirmed` 与 `agent_completion.py` 支持同一合法迟到 `confirmed` 载荷幂等重放且不触碰更高代次新会话；
   - 区分 `setup_network` 承载 `wifi` 与 `usb_net` 两种池，仅在任务绑定 `usb_net` 会话（或显式上报 `usb_net`）时计入 `usb_net` 足迹，避免 `wifi` 任务误记 `usb_net` 欠账；
   - 新增手动会话强制释放（`POST /api/v1/network-supply/sessions/{id}/release`）、Agent 释放回报（`POST /api/v1/agent/network-sessions/{id}/release-report`）、专用审计放行（`POST /api/v1/network-supply/sessions/{id}/audit-override`）与撤销确认（`POST /api/v1/agent/network-sessions/{id}/revoke-ack`），并通过心跳下发 `pending_manual_network_releases` 与 `pending_usbnet_revokes`：
     - 手动 `wifi` 失联不写 `device_return_debt`，保持 `RELEASE_FAILED` + 同代次 `manual_network_supply` 租约等待重连回调或专用审计放行；
     - 手动 `usb_net` 未确认写入 `device_return_debt(source_kind="manual_network_session")` 并将 `manual_network_supply` 租约原地转为标准 ADR-0061 欠账租约（`N → N+1`）；
     - 专用审计放行在单事务内收口会话（`manual_override`）、关闭 `usb_net` 欠账并记 `unverified(override)`、按全设备余债释放或保留/转换租约、写入严格审计日志（`network_session_audit_override`），并为 `usb_net` 下发 `usbnet-revoke` 指令。
3. **过期手动会话定时巡检**（`backend/scheduler/network_session_reaper.py`）：
   - 按显式全序 `(device_id ASC, id ASC)` 取锁，将到期（`expires_at <= now`）的 `PROVISIONING`/`ACTIVE` 手动会话推进为 `RELEASING`，不提前释放租约也不标 `RELEASED`；注册为 `app_scheduler` 单例作业 `network_session_reaper`。

## Alternatives

- **将手动释放与审计放行直接堆入 `backend/services/device_return.py`**：该文件已超过 1900 行且与并行在途工作面 `B12-U4` 重叠。抽离为纯叶节点模块 `backend/services/network_return.py` 既降低合并冲突面，又满足 C5 服务层无环约束与 `#738` inner-imports 棘轮。
- **把手动 `wifi` 失联也塞进 `device_return_debt`**：ADR-0066 方案 6 已否决——`wifi` 未在 ADR-0061 `ONBOARDED` 登记，写入欠账会被 Agent 过滤且无法清偿；保留同代次 `manual_network_supply` 租约配合心跳待回收清单与专用审计放行更简洁一致。
- **新增表列存储待下发 `usbnet-revoke` 状态**：B13 §3.2 明确 U2 不新增 Alembic 迁移。利用现有 `device_network_session.prior_network_snapshot` JSONB 标记 `_usbnet_revoke_pending` 并设置 35 分钟窗口（长于 `usbnet_grant` 1800s 最大 TTL）配合 `revoke-ack` 端点，零迁移即满足撤销义务。

## Verification

- 契约守卫与 Agent 归还单测（隔离无库模式，经 `scripts/run_pytest.py`）：
  `tests/test_device_return_u3_sourceguard_3728.py`、`tests/test_admin_only_read_surface_register.py`、`tests/test_lock_order_collection_total_order.py`、`backend/tests/test_audit_resource_type_guard.py`、`backend/tests/test_audit_action_retention_guard_3017.py`、`tests/test_env_inventory.py`、`tests/test_env_example_parity.py`、`tests/test_settings_scheduler.py`、`backend/tests/realtime/test_p3_3_multi_instance.py`、`backend/agent/tests/test_onboarded_gpu_b7_u1_3810.py`、`backend/agent/tests/test_powercycle_disarm_transient_3989.py`、`backend/agent/tests/test_device_return_u1_3726.py` → `185 passed`；
- 隔离 PostgreSQL 集成测试（`unset TEST_DATABASE_URL`，经 `scripts/run_pytest.py`）：
  - `backend/tests/services/test_b13_u2_network_return.py`（覆盖 B13 §4.2 U2 全部 5 项反例 + WiFi 无误建债 + 共存欠账审计放行 + 到期巡检 + 通用 `apply_override_sync` 未转欠账手动 `usb_net` 租约收口与 `wifi` 隔离反例）→ `10 passed`；
  - `backend/tests/services/test_b13_u1_network_supply.py`、`backend/tests/services/test_device_return_u3_3728.py`、`backend/tests/services/test_device_return_lock_order_3728.py`、`backend/tests/api/test_onboarded_teardown_shape_u5_3730.py`、`backend/tests/api/test_disarm_policy_u8_3752.py` → `94 passed`；
- 静态与治理门禁：`compileall -q backend tools scripts`、`ruff check backend tools scripts tests`、`tools/dev/check-internal-ip-leak.py --check`（4450 文件）、`tools/dev/check_governance_surface.py --base origin/main`（S1–S15、S5x）、`env_inventory`（302 个读取名）全部通过；
- GitHub required checks（`lint`、`CodeQL`、`pr-typecheck`、`pr-compileall`、`pr-agent-tests`、`pr-migrate-empty-db`）：pending（PR 推送后由 CI 执行）。

## Revisit

- **U4 接入 `teardown_network@1.0.0` 与主机 opt-in 能力后**：在 `backend/agent/contracts/device_return.py` 开启 `usb_net` 的 `init_check` 路径（按主机 `"usb_net"` 能力门控），并接入 `ReturnDebtConsumer` 消费心跳 `return_debts[*].usbnet_sessions`、`pending_manual_network_releases` 与 `pending_usbnet_revokes`。
- **U5 前端页面上线后**：对接 `/api/v1/network-supply/sessions/{id}/release` 与 `/audit-override` 并同步 `frontend/src/utils/api/types.ts`。
