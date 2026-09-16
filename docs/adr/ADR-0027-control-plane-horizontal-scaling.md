# ADR-0027: 控制面水平扩展（Leader Election + 多实例）

- 状态：Accepted（P3-1 / P3-2 / P3-3 代码已落地；生产多实例仍为 **opt-in**，见 ADR-0025 D1）
- 版本记录：v1.1（2026-09-08）leadership 失败策略 fail-open → 按 deployment 形态分级（R01-F10/#890）/ v1.2（2026-09-11）多实例清单增补 RunConsole 单实例约束（#1114，R11-F06）/ v1.3（2026-09-11）「可不 sticky」加 transport 前提：Agent websocket-only（#1121，R11-F13）/ v1.4（2026-09-13）新增 RunConsole 归属注册表 P3-4（P1：全局 run_key 互斥 + owner 登记，#1737） / v1.5（2026-09-13）P3-4 **P2**：状态快照落地——跨实例 status/订阅校验生效，剩余限制收窄为 cancel/read_log（#1737） / v1.6（2026-09-13）P3-4 **P3**：跨实例 cancel 转发（请求位 + 有界等待 ack），剩余限制收窄为 read_log（#1737） / v1.7（2026-09-13）P3-4 **P4**：跨实例日志 replay（文件源 + 共享 log_root 前提 + `replay_unavailable` 显式化），#1737 分阶段全部落地 / v1.8（2026-09-16）多实例清单增补第 7 条：**merge（`run_merge_sync`）为实例绑定操作**——本机 `flock` + 本机工具目录，跨路径（手动 API × SAQ）无跨实例互斥；仅登记限制 + 启动 WARN，不预设解除时机（#2189）
- 优先级：P2
- 目标里程碑：M6
- 日期：2026-07-20
- 决策者：平台研发组 / 架构组
- 标签：水平扩展, leader election, APScheduler, SocketIO, 单进程约束

## 背景

ADR-0002 / ADR-0018 强制控制面**单进程**：APScheduler、SAQ in-process worker、SocketIO 同驻 FastAPI。ADR-0025 D1 将「水平扩展类改动」推迟，重启条件为设备池 >80、需要零停机滚动、或多控制平面。

ADR-0026 将 P3 标为远期方向：

1. 准入 pump 的 leader election
2. SocketIO Redis adapter / Centrifugo
3. 控制面多实例（解除全部后台 job 的单实例假设）

本 ADR 承接该方向：先落地最小安全原语，再正式化多实例守卫；**默认仍单实例行为零变化**。

## 决策

### P3-1（已落地）：Scheduler singleton job 的 Postgres advisory-lock leader election

- 模块：`backend/core/leader_election.py`
- 接线：`admission_pump.pump_admission_tick`、`counter_reconciler.reconcile_plan_run_counters_once`（函数内）
- 机制：`pg_try_advisory_lock(stable_key(job_name))`，锁仅在 tick 期间持有
- 开关：`STP_SCHEDULER_LEADER_ELECTION`（默认 `1`）
  - 单实例：永远抢到锁，行为与改造前一致
  - 多实例误部署：至多一个进程跑 singleton tick
  - `0`：关闭选举（调试/应急）——**显式豁免路径**：遗留单进程模式，单进程
    即无双跑，fail-open 仅存于此
  - SQLite / `TESTING=1`：恒为 leader（本地与单测，非多实例形态）
  - **Postgres 下 session 工厂 / 取锁失败 → fail-closed（跳过本轮 tick，
    R01-F10/#890）**：DB 不可用期间 singleton job 本就依赖同一 DB，跳过无
    可用性损失；多实例下 fail-open 会让全部副本同时自认 leader——双跑风险
    不对称地大于跳过成本。DB 恢复后 tick 自动恢复。故障注入测试：
    `tests/test_leader_election.py`

### P3-2（已落地）：SocketIO Redis adapter

- 模块：`backend/realtime/socketio_redis.py` → `create_sio_server()` 可选挂载 `AsyncRedisManager`
- 开关：`STP_SOCKETIO_REDIS_ADAPTER`（默认 `0`，**opt-in**）
  - `0` / 未设：进程内 manager，单实例零 Redis pub/sub 开销
  - `1`：用 `REDIS_URL` + channel `STP_SOCKETIO_REDIS_CHANNEL`（默认 `stp-socketio`）
  - `TESTING=1`：强制关闭（单测不连 Redis）
- `/health` 暴露 `socketio_redis_adapter_enabled` 布尔字段（2026-08-29 `d2b2cea5` 前为 `socketio_redis_adapter`）
- **已覆盖**：dashboard room 广播、`emit_agent_control`（`room=agent:{host_id}`）跨进程 fan-out

### P3-3（已落地）：控制面多实例正式化守卫

#### 全量 APScheduler singleton leadership

- `backend/scheduler/app_scheduler.py`：`_instrumented(..., singleton=True)` 对下列 job 包一层 `hold_scheduler_leadership`：
  - recycler / session_watchdog / device_lease_reconciler / cron_check / retention_cleanup
  - precheck_reaper / plan_chain_reconciler / revoked_token_cleanup / auto_archive_sweep
- **不**二次包裹 `admission_pump` / `counter_reconcile`（它们已有函数内 leadership；不同 Session 上嵌套同名 advisory lock 会导致 tick 误跳过）
- **不**包裹 `saq_queue_depth_poll`（每实例采样本进程可见的队列深度，可并行）

#### 共享 Agent sid 注册 + RPC 去 sticky

- 模块：`backend/realtime/agent_sid_registry.py`
- 开关：`STP_AGENT_SID_REGISTRY`（默认跟随 Redis adapter；可显式 `0`/`1`）
- connect 时 Redis `SET stp:agent:owner:{host_id}`（TTL 默认 120s）；disconnect 时 CAS delete
- `call_agent_rpc`：本地 sid 优先；否则在 adapter 开启时走 `room=agent:{host_id}`（Redis manager 投递到持有连接的实例）；registry 开启时先查 owner，无登记则立即 `AgentNotConnectedError`（避免空 room 挂满 timeout）
- `/health` 暴露 `agent_sid_registry_enabled`（同上改名）

#### 与 ADR-0018 不变量 4

- **修订**：多实例部署在同时满足本 ADR 守卫时被允许（见 ADR-0018 修订记录）。
- **默认**：仍推荐单进程；未开 adapter / 未开 leader election 时行为与历史一致。
- SAQ in-process worker 仍可每实例各跑一个（共享 Redis 队列，由 SAQ 本身去重消费）；`STP_ENABLE_INPROCESS_SAQ=0` + 外部 worker 仍是可选拓扑。

### P3-4（P1–P4 已落地）：RunConsole 归属注册表（#1737）

- 模块：`backend/realtime/console_registry.py`——**同步** Redis 客户端（`RunConsole` 是
  同步线程模型，且会被事件循环线程直接调用；`run_coroutine_threadsafe` 桥接会在循环
  线程内死锁），形态与 P3-3 `agent_sid_registry` 同族（TTL + 指纹 CAS Lua）。
- 开关：`STP_CONSOLE_REGISTRY`（默认跟随 Redis adapter；`0`/`1` 显式覆盖；`TESTING=1` 恒关）；
  TTL `STP_CONSOLE_REGISTRY_TTL_SECONDS`（默认 120s；续期间隔 = TTL/3）。
- 键：`stp:console:key:<run_key>`（互斥；`SET NX PX` 获取 + Lua CAS 续期/释放）/
  `stp:console:owner:<run_id>`（owner 登记；renew-or-rebuild 同 #1113）。
- 语义（裁决草案：[`docs/design/2026-09-13-run-console-multi-instance-ownership.md`](../design/2026-09-13-run-console-multi-instance-ownership.md)，方向 A）：
  - **获取 fail-closed**：注册表不可用 → 拒绝启动（不静默降级为本地互斥）；
  - **续期 `lost`**（确认外部持有/键丢失）→ **止损取消**本 run，保「同 key 全局至多一个
    RUNNING」不变量；纯瞬态错误（`unavailable`）仅告警、等下个 tick，不误杀；
  - **owner 失联**：TTL 过期后 `run_key` 自动可再获取（失联窗口 ≤ TTL）。
- **P3（cancel 转发，v1.6）**：请求方 `SET stp:console:cancelreq:<run_id>`（带 `requested_at` 指纹）→ owner 的 **control tick（默认 1s）** 消费并执行本地取消语义 → 回写 `stp:console:cancelack:<run_id>`（同指纹）；请求方有界等待（默认 3s，`STP_RUN_CONSOLE_CANCEL_WAIT_SECONDS`），超时/注册表不可用 **fail-closed**（绝不假装成功）。等待发生在**线程池/定时器线程**（同步路由）；事件循环内的调用方（`ai_assistant.cancel_action`）经 `asyncio.to_thread`，不阻塞循环。
- **P4（跨实例 replay，v1.7）**：**不引入 RPC 与日志外置**（评估结论见 design 草案 §6）——`read_log` 的既有文件回退即 replay 源（每行落盘、文件行号=seq），跨实例读取的**部署前提是 `STP_RUN_CONSOLE_LOG_ROOT` 对各实例可见**；status 由 P2 快照补全；文件缺失/落后于 owner 快照 seq 时返回 `replay_unavailable: true` + 告警。
- 分阶段：**P1 全局互斥 + owner 登记** → **P2 状态快照** → **P3 cancel 转发** → **P4 跨实例 replay（本次）**——#1737 分阶段全部落地。

## 生产多实例检查清单（opt-in）

1. `STP_SCHEDULER_LEADER_ELECTION=1`（默认）
2. `STP_SOCKETIO_REDIS_ADAPTER=1`
3. `STP_AGENT_SID_REGISTRY` 保持默认（跟随 adapter）或显式 `1`；`STP_CONSOLE_REGISTRY`
   同款（v1.4–v1.7 / P3-4，P1：互斥 + owner 登记；P2：状态快照；P3：cancel 转发；P4：replay）
4. Postgres + Redis 可达；LB 可不 sticky **的条件**（v1.3 / #1121）：Agent 强制
   `transports=["websocket"]`（单条长连接=会话天然亲和）+ LB/nginx 支持 WS upgrade；
   浏览器端 WS-first，**polling 回退路径需会话 sticky**。无 sticky 的端到端 RPC
   验收仍按 [adr-0026 rollout 清单](../operations/adr-0026-admission-and-scale-gray-rollout.md)
   的未勾选项执行
5. 仍以 ADR-0025 D1 重启条件为准，勿过早扩容
6. **RunConsole 跨实例边界**（v1.2 / #1114 → v1.4 / #1737 P3-4）：按
   `STP_CONSOLE_REGISTRY` 状态区分——
   - **未启用**（默认跟随 adapter；显式 `0` 时）：与本条 v1.2 语义一致——dedup Jira
     `run_key` 串行、Agent 安装 console、AI 助手 console 动作与日志、`console:` 房间
     订阅均为**单实例语义**，使用这些功能的部署禁止启用多实例（或先把相关会话 sticky
     到单实例）；跨实例 console 操作返回可诊断错误（详情含 `#1114`），启动输出
     `multi_instance_mode_enabled ... ref=#1114` WARN。
   - **启用后**（P3-4 P1–P4，**不再要求单实例**）：`run_key` **全局互斥**（fail-closed；
     确认失锁止损取消）、owner 登记、**状态快照**、**cancel 转发**与**日志 replay**
     跨实例生效——`status()` / `console:` 房间订阅校验 / `cancel()`（有界等待 ack，
     超时 fail-closed）/ `read_log()`（文件源，需共享 `STP_RUN_CONSOLE_LOG_ROOT`）均可用；
     日志目录未共享时 replay 显式返回      `replay_unavailable` + 告警（不伪装成「没有输出」）。
7. **merge（`run_merge_sync`）为实例绑定操作**（v1.8 / #2189）：merge 的串行原语是**本机**
   `flock`（`{工具目录}/merge_result/.stp_merge.lock`，见 `dedup_scan._exclusive_merge_tool_lock`），
   工具目录取自控制面本机的 `STP_BACKEND_DEDUP_SCAN_SCRIPT` 父目录——**两者都只在同一实例内生效**。
   故多实例下同一 run 的**手动 API（`POST /plan-runs/{id}/dedup/merge`）× SAQ `merge_task`**
   两条路径**没有跨实例互斥**，可各自在本机跑工具、各自向中心同一路径发布。边界与现状：
   - **SAQ 作业本身不会双跑**（本 ADR：共享 Redis 队列由 SAQ 自身去重消费）→ 缺口只出现在
     **跨路径并发**；这也是它比第 6 条更难被发现的原因——**两条路径各自看都是"串行"的**；
   - **artifact 行不会重复**：`_register_merge_artifacts` 带 `storage_uri` 存在性检查，
     故风险是**中心产物归属不确定**（内容取决于各自输入快照的 round / waterline 过滤），非行重复；
   - **守卫现状**：启动输出 `multi_instance_mode_enabled merge_instance_bound=true ... ref=#2189`
     WARN（形态同第 6 条）；**未做跨实例互斥**——属**显式限制而非默认行为**；
   - **解除路径**（两选一，依据见提案 §3.1）：**B2** = 复用 P3-4 的 `run_key` 全局互斥原语做跨实例互斥；
     **B1** = 把 merge 迁到 worker/Agent（**归 ADR-0033**，需其 D1/D3 落地，且其 §5.4 禁止新增
     工具私有路径键）。本条只**登记限制**，不预设解除时机——重启评估触发条件：多实例决定启用 /
     ADR-0033 Phase 2 启动 / merge 成为吞吐瓶颈；
   - **同族待取证**：`extract`（`POST /plan-runs/{id}/dedup/extract` × SAQ `extract_task`）为同一
     双路径形态且写**共享**中心目录；**本条不对其下结论**，单列评估（避免只修 merge 而漏同形态处）。

## 与 ADR-0025 D1 的关系

- **不推翻**「设备池未达阈值前不强制多实例」的产品判断。
- **补齐**误扩容时的安全网：全部 singleton APScheduler job 至多一跑；Redis adapter 后 dashboard / agent room 不分裂；sid registry + room RPC 去掉 sticky 依赖。

## 后果

- 正向：P3 三段可合并；单实例默认零行为变化；正式多实例路径可文档化。
- 负向：advisory lock 依赖 Postgres；Redis adapter / registry 增加 pub/sub 与 key 流量；跨实例 RPC 依赖 python-socketio Redis manager 的 room ack 路径。
- 回滚：`STP_SCHEDULER_LEADER_ELECTION=0` / `STP_SOCKETIO_REDIS_ADAPTER=0` / `STP_AGENT_SID_REGISTRY=0` / `STP_CONSOLE_REGISTRY=0` 或 revert 对应接线。

## 关联

- 扩展 [ADR-0026](./ADR-0026-plan-execution-scaling.md) P3
- 修订 [ADR-0018](./ADR-0018-infrastructure-layer-framework-adoption.md) 不变量 4（条件放宽）
- 对齐 [ADR-0025](./ADR-0025-phase4-architecture-alignment.md) D1 重启条件
- 迁移纪律 [ADR-0008](./ADR-0008-schema-migration-governance-alembic-only.md)（本步无 schema）

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-07-20 | 初稿 Proposed；P3-1 advisory-lock leader election 落地 |
| 2026-07-20 | P3-2：`AsyncRedisManager` opt-in（`STP_SOCKETIO_REDIS_ADAPTER`）；文档诚实边界（RPC sticky） |
| 2026-07-20 | P3-3：全量 singleton schedule leadership + Agent sid registry + room RPC；状态 → Accepted；解除 sticky 依赖 |
| 2026-09-08 | v1.1（R01-F10/#890）：Postgres 形态下 leadership 获取失败 fail-open → **fail-closed**（跳过 tick）；fail-open 仅保留于显式禁用（遗留单进程）与非 PG 形态两条文档化豁免路径；故障注入测试 `tests/test_leader_election.py` |
| 2026-09-11 | v1.2（#1114/R11-F06）：多实例检查清单增补第 6 条——RunConsole 依赖功能（dedup 串行 / 安装 console / 助手 console / console 房间）为单实例语义；跨实例 console 操作改可诊断错误 + 启动 WARN（未做 owner 路由，属显式限制而非默认行为） |
| 2026-09-11 | v1.3（#1121/R11-F13）：清单第 4 条「LB 可不 sticky」加前提——Agent websocket-only（实现同步改 `transports=["websocket"]`；此前默认 polling 优先，会话亲和与无 sticky 冲突）+ 浏览器 polling 回退需 sticky；无 sticky 端到端验收仍归 rollout 清单 |
| 2026-09-13 | v1.4（#1737/P3-4）：新增 RunConsole 归属注册表（`STP_CONSOLE_REGISTRY`）——**P1 已落地**：全局 `run_key` 互斥（`SET NX PX` + Lua CAS 续期/释放；获取 fail-closed；确认失锁止损取消）+ owner 登记（renew-or-rebuild）；清单第 6 条按注册表状态区分（未启用=单实例语义；启用=互斥/登记跨实例，status/cancel/read_log 为 P2/P3 剩余限制）；裁决草案 `docs/design/2026-09-13-run-console-multi-instance-ownership.md`（方向 A / 窄化自杀 / TTL 120s） |
| 2026-09-13 | v1.5（#1737/P3-4 P2）：状态快照落地——owner 端在 start/终态/tick 发布 `stp:console:status:<run_id>`（SET EX；终态 TTL=本地终态保留期；tick 续期，丢失即重发），跨实例 `status()` 与 `console:` 房间订阅校验读快照生效；剩余限制收窄为 `cancel()` / `read_log()`；告警/404 提示按「注册表启用」分支切换口径 |
| 2026-09-13 | v1.6（#1737/P3-4 P3）：跨实例 cancel 转发——`stp:console:cancelreq/ack` 请求位 + `requested_at` 指纹；owner 端新增 **control tick（默认 1s）** 消费请求并执行本地取消；请求方有界等待（默认 3s）超时 fail-closed；事件循环内调用方改 `asyncio.to_thread`；剩余限制收窄为 `read_log` |
| 2026-09-13 | v1.7（#1737/P3-4 P4）：跨实例日志 replay——`read_log` 文件回退在 `STP_RUN_CONSOLE_LOG_ROOT` 共享前提下跨实例可用；status 由 P2 快照补全；文件缺失/落后显式标记 `replay_unavailable`（不伪装成「没有输出」）；评估驳回控制面间 RPC 与 Redis 日志镜像（文件即 replay 源）；清单第 6 条收口为「启用注册表后不再要求单实例」 |
| 2026-09-16 | v1.8（#2189）：清单增补**第 7 条——merge（`run_merge_sync`）为实例绑定操作**。来源：I-13 方案 B 的优先级重评（提案 §3.1，owner 裁 **B0**）在取证时发现——merge 的串行靠**本机** `flock`（`{工具目录}/merge_result/.stp_merge.lock`）、工具目录取自本机 `STP_BACKEND_DEDUP_SCAN_SCRIPT` 父目录，而本条清单此前只登记了 RunConsole 依赖功能 → merge 的实例绑定是**未登记的隐性限制**。**边界澄清**：SAQ 作业不会双跑（共享 Redis 队列由 SAQ 去重消费），缺口仅存在于**跨路径并发**（手动 API × SAQ），两条路径各自看都为「串行」故此前未被发现；artifact 行因 `storage_uri` 存在性检查不重复，风险是中心产物归属不确定。**本轮只登记 + 启动 WARN（`merge_instance_bound=true ... ref=#2189`），不做跨实例互斥**——解除路径 B2（复用 P3-4 `run_key` 原语）/ B1（迁 worker，归 ADR-0033）与其触发条件见提案 §3.1。`extract` 为同族形态（写共享中心目录），登记为待取证，不并入本条 |
