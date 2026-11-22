# B10 U3：控制面 Redis 全局传输并发许可 API 与 Agent 客户端

Status: implemented
Class: feature

关联：[#4086](https://github.com/DUElost/stability-test-platform/issues/4086)（批次方案 v1.5）、
[#4076](https://github.com/DUElost/stability-test-platform/issues/4076)、
ADR-0064 D3（Accepted v1.0，见 PR [#4085](https://github.com/DUElost/stability-test-platform/pull/4085)；合入 `main` 后补本地路径锚）。

## Decision

按 `#4086` §3 U3 与 ADR-0064 D3 落地控制面瞬时传输闸门，不建 PG 表、无 Alembic 迁移：

1. **控制面**：`POST /api/v1/agent/transfer-permits/{acquire,renew,release}`（`X-Agent-Secret`），
   Redis Lua 原子维护池 `firmware_nfs` 槽位；`lease_token` + `host_id` CAS；
   `STP_FIRMWARE_TRANSFER_MAX_HOSTS` 默认 2、`STP_FIRMWARE_TRANSFER_PERMIT_TTL_SECONDS` 默认 60，
   收敛到 `backend/core/settings/transfer.py`。
2. **Agent**：独立模块 `backend/agent/transfer_permit_client.py`（供 U2 `firmware_cache`
   注入），构造参数承接等待/不可达/TTL 配置，不改 `settings.py`；上下文持有者 + TTL/3
   续期 + finally 释放；排队超限恒 `transfer_permit_wait_timeout`，不可达超限默认
   `transfer_permit_unavailable`，仅显式 `allow_permit_bypass=True` 才 `permit_degraded`。

## Alternatives

- **把客户端写进 `firmware_cache.py`（ADR 字面归属）**：本单元与 U2 并行，提前改
  `firmware_cache.py` / `settings.py` 必冲突——否决；客户端独立落位并在 PR 偏离说明披露，
  U2 注入集成。
- **PG 表承载许可**：违反 `AGENTS.md` Redis 边界与 ADR-0064 D3「不得新增 PG 业务表」——否决。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_transfer_permit_b10_u3.py backend/tests/api/test_transfer_permits_b10_u3.py backend/agent/tests/test_transfer_permit_client_b10_u3.py -q` → **16 passed**
- `./scripts/project_python.sh tools/dev/check_governance_surface.py --base origin/main --check` → **OK**
- `./scripts/project_python.sh scripts/run_gates.py check:quick` → **16 gates OK**
- 确认 diff 无 `backend/alembic/versions/` 新增迁移。

## Revisit

- U2 将本客户端注入 `FirmwareCacheManager.ensure_firmware`（cache miss + `.host-transfer.lock` 内）；
  Agent 侧 `STP_FIRMWARE_TRANSFER_*` 等待旋钮随 U2 收敛进 `backend/agent/settings.py`。
- 控制面部署后本 API 即生效（无 DB 迁移）；端到端并发限流验证属 B10 §5 激活步骤。
