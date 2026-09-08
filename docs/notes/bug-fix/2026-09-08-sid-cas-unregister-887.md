# Agent SID registry 注销/续租原子化（compare-and-delete/expire）

Status: implemented
Class: bug-fix

## Decision

#887（R01-F07）：`unregister_agent_owner` 是 GET → 比较 → DELETE 三步，中间有
异步让出点——旧连接 unregister 前若新连接已 `register`（SET 新 payload），旧
连接仍无条件 DELETE，新登记丢失 → 跨进程 RPC 误判 Agent 离线。

修复：比较与写合并为 Redis Lua 脚本（单线程原子执行）——

- `_CAS_DELETE_LUA`（unregister）/ `_CAS_EXPIRE_LUA`（renew）：GET 当前值与
  ARGV 期望值整串比对，相等才 DEL/EXPIRE，否则返回 0；
- 期望值经 `_owner_payload(host_id, sid)` 构造——`json.dumps` 键序确定
  （instance_id/sid/host_id），与 register 写入逐字节一致，整串比对同时覆盖
  sid 与 instance_id 两字段（跨进程天然不匹配）；
- **`renew_agent_owner` 的同型竞态一并收口**：#881 引入的续租同为
  GET→比较→SET，GET 后被新登记覆盖会把旧 payload 续期写回、同样覆盖新登记
  ——同一原子原语的两面，一并修复（值不匹配即 no-op，返回 False）；
- `register` 保持无条件 SET（最后写者赢，新连接覆盖旧登记是正确语义）；
- 对外签名与返回语义不变（renew 仍 bool：执行了续租为 True）。

FakeRedis 影响面：`test_p3_3_multi_instance.py` 两处 fake 缺 `eval`，按参考
语义补齐（含 `_TtlFakeRedis` 的虚拟时钟过期模型）；行为契约变更随既有测试
同步更新。

## Alternatives

- WATCH/MULTI 乐观锁：等价原子但需重试循环，Lua 单脚本更简单且是 issue 首选；
- GETDEL+版本校验：不可行——GETDEL 无条件删，取到非期望值时新登记已被误删；
- 仅修 unregister 不动 renew：放弃——同根因（非原子比较-写），只修一半留下
  renew 覆盖新登记的活竞态。

## Verification

- `pytest backend/tests/realtime/test_agent_sid_registry.py`：8 passed（新文件
  ——交错重连不误删 / 跨进程同 sid 不互删 / renew 不复活不覆盖 / 缺键 no-op）；
- `pytest backend/agent/tests/test_p3_3_multi_instance.py`：15 passed（CI 同款
  env：TESTING=1 + 占位 DATABASE_URL；两处 FakeRedis 补 eval 参考语义）；
- **Lua 脚本语义真 Redis 实测**（docker redis:7-alpine 一次性容器）：CAS-DEL
  不匹配 spare 新值 / 匹配删除 / CAS-EXPIRE 续期 TTL=120 三场景全过；
- ruff 干净；`backend/tests/realtime/` 全目录 67 passed；
- Registry：fix-887-sid-cas-unregister 全程登记（--issue 887）。

## Revisit

- TTL 边界（EXPIRE 与 GETDEL 的时钟竞争在 Lua 内部已原子，无外部让出）；
- 若未来 registry 引入多 key 原子操作需求，沿用本单的 Lua 模式扩展。
