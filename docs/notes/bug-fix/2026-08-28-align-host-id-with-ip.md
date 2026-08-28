# 对齐 host.id 与迁移后 IP（2026-08-15 网段迁移历史杂症）

Status: implemented
Class: bug-fix

## Decision

2026-08-15 网段迁移后，34 台 host 的 IP 全部迁至 172.21.15.x，但其中 20 台的
`host.id`（前端显示的主机标识）仍保留旧 8.x/9.x 网段号（如 `.80` 的 id 仍是
`172-21-8-192`），与其当前 `host.ip`（`172.21.15.80`）不一致——按编号找机
与按 IP 认机出现二义。

用一次性 Alembic 数据迁移 `k8l9m0n1o2p3` 将所有 `host.id` 统一为
`172-21-15-{ip 末段}`（与已迁移的 13 台同约定）。映射由 `host.ip` 末段在库里
现算，不硬编码，杜绝漂移。

改写用 **ON UPDATE CASCADE 方案**（非逐表刷子表）：
1. 将 6 个 FK 约束（`device` / `device_leases` / `job_instance` /
   `device_log_event` / `job_log_signal` / `plan_run_host`）重建为
   `ON UPDATE CASCADE`（`ON DELETE` 照原样保留）。
2. 先把 `old_id → new_id` 映射快照进临时表（改 id 后旧值即不可查）。
3. 单条 `UPDATE host SET id = …`；6 张 FK 子表在同一语句内级联改写，无瞬时
   违反引用完整性。
4. 用映射快照显式改写 2 个无 FK 的快照列（`plan_run_target_device.host_id_snapshot`
   ——ADR-0026 的 host 分组隐性 join 契约，与 `plan_run_host.host_id` 必须成对同步；
   `plan_run_artifact.host_id`——历史报表挂接）。
5. 单事务（alembic transactional DDL）执行，失败整体回滚；`downgrade` 为 no-op
   （反历史规范化只会破坏运行中控制面/Agent 的 room key）。

受影响行量（生产实测）：6 FK 表合计 2310 行 + 快照列 1425 行改写。

## Alternatives

- **逐表先改子表再改父表**：被否决。父 `host.id` 未变时，子表指向的新 id 在
  父表不存在，立即约束（NO ACTION）按语句级 FB 校验直接炸——在 scratch DB 上
  实测复现 `device_host_id_fkey` FK 报错并以事务回滚告终（恰好顺带验证原子性）。
- **临时占位 host 行 + SET CONSTRAINTS DEFERRED**：可行但需多插几张行、依赖
  约束可 DEFERRABLE（现状不可），复杂且引入更多锁；CASCADE 一行父表即达目的。
- **历史快照列保留旧值**（不污染历史）：否决。会使 ADR-0026 历史 plan_run 的
  host 分组 join 与 `host` 表分叉；用户确认历史报表应统一显示新名。
- **改动不留 `ON UPDATE CASCADE`**：保持不留会要求迁移里 drop/add 往返两次
  约束，徒增独占锁窗口；CASCADE 是严格改善，与「读现状约束」语义无冲突。

## Verification

- scratch `postgres:16` 一次性容器（非生产）：`alembic upgrade` 至 `h2i3j4k5l6m7`，
  种子 2 台真实旧号 host 覆盖全部 8 张引用表，再 `upgrade head`，逐表核实
  `172-21-8-192→172-21-15-80`、`172-21-9-121→172-21-15-67`，6 FK + 2 快照列
  全部正确改写、`host` 无旧号残留、中性 host（`172-21-15-20`）不动。
- 约束状态核验：6 个 host.id FK `update_rule=CASCADE`。
- `ruff check` 通过。
- 生产机上未对 `stp` 执行任何 alembic 命令（遵守 AGENTS.md 迁移试验禁令）。

## Revisit

- 合入走 PR，由部署流程跑 `alembic upgrade`；不动手对生产库直改。
- 迁移窗口内控制面/Agent 的 `agent:{old_id}` socketio room 键随 id 变更，
  Agent 需按新 id 重建心跳/连接——属预期一次重连，观测窗口期心跳波动即可。
- **遗留缺口（2026-08-28 修复）**：agent 侧 `/opt/stability-test-agent/.env`
  的 `HOST_ID` 不随迁移更新（hot-update 不覆盖该键）→ Agent 仍以旧 id 注册
  SocketIO room → precheck `verify_scripts` 的 RPC 全判 agent_offline、
  派发循环 requeue（PlanRun #247 实证）。配套批量修复见
  [2026-08-28-agent-host-id-mismatch-after-migration.md](./2026-08-28-agent-host-id-mismatch-after-migration.md)；
  **今后任何 host.id 类迁移必须把 agent 侧 HOST_ID 刷新列为配套步骤**。
