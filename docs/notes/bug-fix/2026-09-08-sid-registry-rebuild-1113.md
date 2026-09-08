# SID 登记丢失后存活连接可重建（#1113）

Status: implemented  
Class: bug-fix

## Decision

`renew_agent_owner` 的 Lua 从「仅 CAS EXPIRE」改为 `RENEW_OR_REBUILD`：
key 缺失时 `SET` 本进程 payload；payload 匹配则 `EXPIRE`；外键不覆盖。
心跳仍是活性证据（`on_heartbeat` 调用），满足「存活连接可恢复跨进程 RPC」
且不抢占其他 instance 的合法 owner。

涉及：`backend/realtime/agent_sid_registry.py`；测试见
`test_agent_sid_registry.py`、`test_p3_3_multi_instance.py`。

## Alternatives

- 仅在调用方 GET 后 SET：仍有竞态窗口，#887 已要求 Lua 原子。
- 缺 key 强制 Agent 重连：可用但体验差，心跳已足够证明存活。
- 保留 #881「过期不复活」：与本 bug 验收冲突；心跳路径应允许重建。

## Verification

- `test_renew_rebuilds_missing_key_for_live_connection`
- `test_renew_missing_key_does_not_overwrite_foreign_owner`
- `test_renew_does_not_overwrite_foreign_keys`
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/realtime/test_agent_sid_registry.py
  backend/agent/tests/test_p3_3_multi_instance.py -q`

## Revisit

若需区分「主动 unregister 后误重建」，可加短冷却或 disconnect 标志。
