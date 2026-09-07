# Agent SID registry 活跃续租（#881 / R01-F01）

Status: implemented
Class: bug-fix

## Decision

修复「连接存活超过 TTL（默认 120s）后，跨进程 RPC 因登记过期被拒、预检误判 `agent_offline`」（#881）：原实现只在 connect 时 `SET … ex=TTL`，之后无任何续租——长连接必然在 TTL 后对非 owner 进程「消失」。

三层修复：

1. **`renew_agent_owner(host_id, sid)`**（agent_sid_registry.py）：owner 进程对活跃连接续租。**双重匹配才续**——payload 的 `sid` 与 `instance_id` 都必须与当前调用匹配：不复活他人 instance 的 key、不复活已过期 key、host 被新连接接管后旧 sid 不得续；
2. **接入点 = owner 进程的 `on_heartbeat`**（Agent 心跳 = 最可靠的连接活性证据，且周期 < TTL 120s——连接存活期间 key 永续，agent 离线后 TTL 自然过期、语义不变）。调用在 devices 空列表 early return **之前**（心跳本身即活性，与设备列表无关）。跨进程 RPC lookup 路径**不续租**：非 owner 进程无法确认连接真活着，续租会造成幽灵窗口；
3. **回归测试**：新增 `_TtlFakeRedis`（原 FakeRedis 忽略 `ex=`，无法覆盖过期——issue 已指出）基于虚拟时钟的 TTL 语义，两个用例覆盖验收三项：跨 TTL 后跨进程可达（renew 生效）、他人 key 不复活、过期 key 不复活、sid 不匹配不续。

## Alternatives

- **跨进程 RPC lookup 时续租（expire）**——放弃：lookup 进程非 owner，无法确认连接真活；会延长「owner 已死但 key 未过期」的幽灵窗口，与 TTL 的失效语义冲突；
- **RPC 路径（owner 本地）也续租**——放弃：心跳周期（<TTL/2）已足够，每次 RPC 都写 Redis 加大写放大，收益为零；
- **拉长 TTL 而不加续租**——放弃：治标且恶化真故障场景（owner 宕机后误路由窗口 = TTL）。

## Verification

- `_TtlFakeRedis` 两用例：跨初始 TTL 后（renew 后）跨进程可达 ✓；他人 key/过期 key/sid 不匹配三向不续 ✓；
- 全套 15 passed（13 既有 + 2 新增；其中 4 个 call_agent_rpc 基线失败为 JWT 环境变量缺失——R01-F04 同族症状，CI 有变量故绿，与本修复无关）；
- 治理门禁/ruff 通过；多 Harness 批次工具链 dogfood 连续第三单（registry 全周期）。

## Revisit

- agent 心跳周期若某部署 >TTL/2，可为该部署调 `STP_AGENT_SID_REGISTRY_TTL_SECONDS`（≥2×心跳周期）；
- unregister 非原子（R01-F07/#887）同域独立缺口，另行处理；
- P2 Adapter 的 wrapper heartbeat（`ai_work.py heartbeat`）与本文 renew 无耦合（前者是控制面进程的 Execution 登记，后者是 Agent 连接租约）。
