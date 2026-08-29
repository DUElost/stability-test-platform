# k8l9m0n1o2p3 迁移的 agent 侧 HOST_ID 同步缺口

Status: implemented（2026-08-28 批量修复 34/34）
Class: bug-fix

## Decision

`k8l9m0n1o2p3_align_host_id_with_ip_after_subnet_migration.py` 把控制面
`host.id` 从遗留 `172-21-<旧网段>-*` 对齐到 `172-21-<新网段>-{ip末段}`，但 **agent 侧
`/opt/stability-test-agent/.env` 的 `HOST_ID` 未同步**——Agent 仍以旧 id
注册 SocketIO room（`agent:{host_id}`），控制面按新 id 发 SocketIO RPC
时 `call_agent_rpc` 查不到 sid → `AgentNotConnectedError` → precheck 的
`verify_scripts` 判 `agent_offline` → 派发循环 requeue。

**表现**（2026-08-28 实况）：PlanRun #247 卡 QUEUED，`queue_blockers` 恒为
8 台 host 的 `agent_offline/host_unreachable`；HTTP 心跳全部新鲜（4–23s）、
host.status=ONLINE——心跳与 SocketIO 连接是两条独立通道，心跳正常掩盖了
room 键失配。

## Alternatives

- 只重启 agent 不修 HOST_ID：无效（重启后仍用旧 id 注册 room）。
- 在控制面兼容旧 id 查 sid：掩盖问题，且旧 id 行已不存在，无法映射。

## Verification

- 34 台批量 `sed` 重写 `.env` 的 `HOST_ID` 为 ip 派生新 id + 重启 agent。
- PlanRun #247 立即放行：32/32 COMPLETED，认领日志
  `pending_jobs_fetched count=5 slots=5`（旧值 count=17 slots=17）。
- 复扫 host 表：`host.id == ip 点转横杠` 全部一致（0 不匹配）。

## Revisit

- 任何「改 host.id」类迁移（网段迁移、重命名）必须把 **agent 侧 HOST_ID
  刷新**列为迁移配套步骤；hot-update 不覆盖 HOST_ID（它不在 env 覆盖集），
  需按机 SSH 处理或新增下发键。
- 控制面侧可加一条 precheck 防御：verify_scripts 的 offline 判定与心跳
  状态冲突（心跳新鲜但 RPC 离线）时，记 `agent_sid_mismatch` 告警而非
  静默 requeue——便于此类问题第一时间暴露。
