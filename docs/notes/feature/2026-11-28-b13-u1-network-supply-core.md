# B13 U1：统一网络供给数据模型、加性迁移、双入口互斥、三时点交接与 Ed25519 `usbnet_grant` 签发/冷却

Status: implemented
Class: feature

## Decision

按批次 B13（`#4212` v1.3）与 ADR-0066 D1–D3、D5.3、D6.3 落地控制面核心数据面与服务层（合入后默认无主机启用 `usb_net`，现网零影响）：

1. **加性迁移与 ORM（`b13u1c2d3e4f5_network_supply_schema.py`，父 revision `b12u1a2b3c4d5`）**：
   - 新增单数业务表 `device_network_session`（`backend/models/network_session.py`），落地 D2.2 精确 PostgreSQL `CHECK` 约束 `ck_device_network_session_lease_binding`（仅 `source='job' AND status='RESERVED'` 与 `source='job' AND status='RELEASED' AND release_reason='unclaimed_canceled'` 可空租约）与活跃部分唯一索引 `uq_device_network_session_active_per_device`；
   - 在 `host` 表新增显式列 `usbnet_max_devices` 与 `usbnet_min_allowed_iat_by_egress`（不落 `Host.extra` 裸键）。
2. **统一网络供给池校验、正交出口与剥密（`resource_pool.py` / `routes/resource_pools.py`）**：
   - 支持 `resource_type ∈ {"wifi", "usb_net"}` 与正交 `config.egress ∈ {"direct", "vpn"}`（Phase 1 拒绝 `usb_net + vpn`，允许 `wifi + vpn`）；
   - `/api/v1/resource-pools/available` 保持白名单剥密，并新增 `required_type` / `required_egress` 交集过滤；
   - 目标池全局并发配额按 `device_id` 并集去重（`device_network_session ∪ resource_allocation`），`usb_net` 单机硬上限跨池、双入口、含 `RELEASING`/`RELEASE_FAILED` 统一计数。
3. **存量兼容与新旧混排（`plan_wifi.py` / `plan_dispatcher_*.py` / `routes/plans.py` / `ai_assistant/`）**：
   - 派发请求支持 `network_pool_id` 并保留 `wifi_pool_id` 兼容别名（双传不一致报 400）；
   - 纯存量 Plan（`connect_wifi`/`monkey_setup`）拒绝 `usb_net` 池，`connect_wifi` 自动选 `wifi(direct)`，`monkey_setup` 无 SSID 跳过，新旧混排按交集校验并拒绝矛盾。
4. **任务三时点交接与手动供网原子互斥（`network_supply.py` / `admission_pump.py` / `agent_claim.py` / `plan_run_abort.py` / `routes/network_supply.py`）**：
   - 任务：物化写 `RESERVED`（空租约）→ 认领绑定 `JOB` 租约转 `PROVISIONING` 并为 `usb_net` 首签 Ed25519 `usbnet_grant` → 未认领取消同事务核销 `RELEASED/unclaimed_canceled`；
   - 手动：`POST /api/v1/network-supply/manual/enable` 沿用 `Host → Device → ResourcePool` 锁序同事务获取 `DeviceLease(MAINTENANCE, reason="manual_network_supply")`，与离线/刷机/VBUS PlanRun 及归还欠账租约严格互斥；同步提供 `manual/{id}/renew` 与 `GET /sessions`（均为 `require_admin`）。
5. **Ed25519 `usbnet_grant` 签发、续签与冷却契约（`network_grant.py` / `core/settings/network_supply.py` / `routes/agent_network_supply.py` / `routes/heartbeat.py`）**：
   - 令牌格式 `<base64url_nopad(canonical_json_utf8)>.<base64url_nopad(raw_64byte_ed25519_sig)>` 与 B13-U3 `stp_agent_priv.py` 字节级一致；TTL 范围 `[300, 1800]` 秒（默认 `900` 秒）；
   - 控制面始终按真实墙钟写 `iat = int(time.time())`（绝不伪造未来 `iat`）；当 `iat <= host.usbnet_min_allowed_iat_by_egress[egress]` 时，手动开启与 `POST /api/v1/agent/network-sessions/{session_id}/grant-renew` 返回可重试 `503 USBNET_EGRESS_COOLDOWN` + `Retry-After`，`agent_claim` 暂缓认领该出口作业；心跳与续签均持久化 Agent 上报的 `min_allowed_iat_by_egress`。

## Alternatives

- 将 `usbnet_min_allowed_iat_by_egress` 塞入 `Host.extra` JSON：否决——违反 ADR-0038/ADR-0066「业务控制字段禁止落 `Host.extra` 裸键」约束，改为 `host` 表显式 JSONB 列。
- 在冷却期内由控制面将 `iat` 往未来上推至 `barrier + 1`：否决——与 `usbnet-attach` 的 `grant.iat <= now + 30` 校验在 `teardown` 同秒冲突形成空集且破坏签名时间戳真实性，改为真实时钟 `iat` + 可观测冷却门控（B13 v1.3）。
- 将 `agent_network_supply.py` 直接追加到 `agent_api.py`：否决——避免膨胀 `agent_api.py` 上帝文件（#736），拆分为独立路由文件并挂载至同一 `/api/v1/agent` 前缀。

## Verification

- `unset TEST_DATABASE_URL && TESTING=1 JWT_SECRET_KEY=test-secret PYTHONPATH=. .venv/bin/python scripts/run_pytest.py backend/tests/services/test_b13_u1_network_supply.py backend/tests/api/test_resource_pools.py backend/tests/services/test_wifi_optional_at_run_time.py backend/tests/services/test_agent_claim.py backend/tests/services/test_vbus_flash_admission_3892.py backend/tests/services/test_vbus_flash_claim_3892.py -q`
- `env -i PATH="$PATH" PYTHONPATH=. TESTING=1 DATABASE_URL="postgresql+psycopg://test:test@127.0.0.1:1/test_unused" .venv/bin/python scripts/run_pytest.py tests/test_env_inventory.py tests/test_env_example_parity.py -q`
- `.venv/bin/python tools/dev/check-internal-ip-leak.py --check`
- `.venv/bin/python tools/dev/check_governance_surface.py --base origin/main`
- `.venv/bin/python -m ruff check backend tools scripts tests`
- `.venv/bin/python -m compileall -q backend tools scripts`
- `git diff --cached --check`

## Revisit

- B13 U2（`device_return.py` 归还欠账、迟到确认与手动审计放行）、U4（Agent `usb_net` 运行时与新脚本族）、U5（前端 `/network-supply` 与 `frontend/src/utils/api/types.ts` 同步）将在 U1 独立复核通过并合入后接续。
- 本 PR 按批次分工未修改 `frontend/src/utils/api/types.ts`（归属 U5，且避免与在途 PR #4217/#4218 冲突）。
