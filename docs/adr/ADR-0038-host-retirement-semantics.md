# ADR-0038：主机退役语义（Host Retirement Semantics）

- 状态：**Accepted**（v0.2：2026-09-13 定稿，9 稿评审 synthesis + 人工裁决 D-1～D-6）
- 版本记录：
  - v0.2（2026-09-13）：按 9 份独立评审（[#1557](https://github.com/DUElost/stability-test-platform/issues/1557)，综合稿 PR #1677）与人工裁决 D-1～D-6 修订；**转 Accepted**。修订对照见 §6。
  - v0.1（2026-09-12 初版；#796/#937 Revisit 触发）
- 优先级：P2
- 目标里程碑：M7
- 日期：2026-09-13
- 决策者：平台研发组（D-1～D-6 人工裁决见 #1557，2026-09-13）
- 标签：生命周期, 软删, 数据保留, 主机, 运维
- 关联：[#796](https://github.com/DUElost/stability-test-platform/issues/796)（触发：DELETE 级联清历史）、[#937](https://github.com/DUElost/stability-test-platform/issues/937)（硬删预检，PR #1380）、[#827](https://github.com/DUElost/stability-test-platform/issues/827)（审查总表）、[#961](https://github.com/DUElost/stability-test-platform/issues/961)（R04 台账）、[#1557](https://github.com/DUElost/stability-test-platform/issues/1557)（评审请求与裁决）、PR [#1677](https://github.com/DUElost/stability-test-platform/pull/1677)（synthesis）、PR [#1720](https://github.com/DUElost/stability-test-platform/pull/1720)（评审会话×模型归属 errata）、ADR-0035（主机身份与凭据）、ADR-0019（设备租约与容量）、ADR-0026（准入队列语义）、ADR-0036（通知投递语义）、#1249/#1250（维护窗口与升级门禁）

## 1. 背景

### 1.1 问题定性：不是「删除要不要挡」，而是用完的主机没有终态

#937（PR #1380）已让 `DELETE /hosts/{id}` 在**有历史依赖时 409 保数据**
（活跃 Job / 历史 Job / 设备 / PlanRunHost 投影，`hosts.py:465-549`）——这
与 #796 的决策一致且正确。其运维约束须按维度区分（v0.2 事实更正）：

- **历史 Job / PlanRunHost 维度是有界的**：终态 Run 默认 **3 天**由
  `run_retention_cleanup` 连 Job/StepTrace/租约/产物一并清理
  （`backend/scheduler/cron_scheduler.py:29` `PLAN_RUN_RETENTION_DAYS=3`、
  `:262`），清理后这两类预检自然放行；
- **设备维度是无界的**：设备行无任何删除入口（`devices.py` 无 DELETE 路由），
  `device.serial` 全局唯一且为租约/Job 的历史锚点 → **有过设备的主机永久
  409**；
- 且当前没有任何状态能表达「这台机器已不再使用」：`status` 是存活信号
  （ONLINE/OFFLINE/DEGRADED），OFFLINE 只表示「当前不可达」，会被下一次
  心跳改写（`heartbeat.py:236` 如实落库）。

现实需求（硬件退役/换新/下线）：机器不再参与调度，但历史必须完整保留。

### 1.2 事实核验（2026-09-13 修订，静态盘点）

- `host.id` **仅在创建时**由 IP 派生（`backend/core/host_identity.py:10`，
  `198.51.100.6` → `198-51-100-6`）；此后 `_sync_host_identity` 只改
  ip/name 不改 id（`heartbeat.py:76-113`）；`Host.ip` 全局唯一
  （`backend/models/host.py:41`）+ 创建显式 409（`hosts.py:197-233`）——
  **判据单向：同 IP ⇒ 复用旧行；同身份 ⇏ 同 IP**。冲突后缀分支
  （`host_identity.py:40-48`）在 id/ip 脱钩场景可达（有测试实证：
  `backend/tests/test_host_identity.py:22`）；
- 心跳**权威路径是 `POST /api/v1/heartbeat`**（客户端 `backend/agent/heartbeat.py:84`；
  `heartbeat_thread.py:27` 自述 SOLE authority），按 id 找行、缺失即建、
  找到即原位复活、并含「按 IP 找回旧行」（`backend/api/routes/heartbeat.py:196-236`；
  v0.1 所引 `agent_api.py:806-829` 系轻量端点 `/api/v1/agent/heartbeat`，
  当前无客户端调用，按 ADR-0026 保留供双通道收敛——**两条端点都必须按 D4
  处理**，「按 IP 找回命中退役行」同样视为活体退役）；
- `Host.extra` 主心跳**每拍按 6 键白名单重建**
  （`heartbeat.py:249-262`）——**不是持久状态容器**，退役告警去重等状态
  不得放裸键（D4）；
- 设备归属由心跳按 serial 全局 re-home（`heartbeat.py:403`，活跃租约阻断
  `:385-392`）——设备物理搬移至新主机无需本 ADR 额外机制；
- 派发与认领都按存活状态收口：dispatcher 排除 OFFLINE
  （`plan_dispatcher_sync.py:155`），claim 要求 ONLINE（`agent_api.py:401`）——
  **D4 之下这两条都不足以保护退役主机**（退役活体仍满足），必须逐点叠加
  `retired_at IS NULL` 判据（D5）；
- 准入队列语义（D-1 事实依据）：非致命拒因一律可重试排队且**正常排队不
  消耗重试计数**（`admission_pump.py:292-293` 原文）、`host_offline` 可自愈、
  `host_maintenance` 有 TTL——退役**永不自愈**，归 retryable 即无上界永久
  QUEUED（`plan_dispatcher_sync.py:68`、`:510-519`，`admission_pump.py:666-676`）；
- 全模型无 `deleted_at` / `is_archived` / `retired_at` 先例（零命中）；
  全仓无 device 删除路径（零命中）；
- 命名冲突：`Host.extra['archive']` 已被 ADR-0025 存储归档遥测占用
  （`agent_api.py:3192`）——本 ADR 用「退役 retire」，不用「归档 archive」；
- `HostStatus.DEGRADED` 是 schema 通道既有值（`schemas/host.py:104`），但
  当前**无控制面写点、Agent 未上报（latent）**；`session_watchdog.py:43-48`
  只收敛 ONLINE ⇒ 停机 DEGRADED 永不转 OFFLINE。D2/D4 不依赖 `status`
  即不受此影响，但实现单不得把 DEGRADED 当「在用」或「可退役」信号。

### 1.3 约束

- **历史不可清**（#796/#937 决策）：本 ADR 不得提供任何静默或显式清空
  job/device/plan_run_host 历史的路径。DB 侧彻底清除（含设备维度）的议题
  **另立裁决**（现行清理是 `run_retention_cleanup`；ADR-0025 只管日志文件
  闭环，**不承接** DB retention/purge）；
- **生命周期与存活正交**：`status` 的 owner 是心跳（Agent 如实上报），
  生命周期不得复用 `status`，否则在线机器会静默改写运维决定（同
  ADR-0026 status/phase 正交先例）；
- 退役必须**可解除**（误操作可恢复）、全程审计；
- 不引入新的保留期/purge 机制。

## 2. 决策

- **D1 单一生命周期真源**：`host` 表新增可空列 `retired_at`
  (TIMESTAMPTZ) / `retired_by` (String(128)，对齐 `models/audit.py:22`) /
  `retire_reason` (Text；**必填**，min_length=1，unretire 亦记因)，
  `retired_at IS NOT NULL` 即退役；NULL = 在用。additive 迁移，无需回填
  （存量全部默认在用）。**不新增 `HostStatus` 枚举值**。事件真源是
  `audit_logs`（三列只承载当前态投影；admin 退役路径**审计失败即事务失败**）。
- **D2 「删除」与「退役」分家（Cordon 前置）**：`DELETE` 语义与 #937 预检
  原样不变（硬删仅对干净主机开放）；新增 `POST /hosts/{id}/retire` 与
  `POST /hosts/{id}/unretire`（admin + 审计）。退役前置 = **无活跃 Job
  （含 QUEUED PlanRun 引用）**；`status` **不进前置**（消除「停 Agent ↔
  新派发」竞态）；**显式不检查 DeviceLease**（retire 不删行，租约由
  reconciler 回收）；retire/unretire 与 claim **同行锁复检**。
  unretire 无前置；写回语义 = 清空 `retired_at`、`retired_by`/`retire_reason`
  保留为最近一次退役痕迹。retire/unretire 幂等。
- **D3 退役即终态，不做带历史硬删**：退役不触发任何删除；有历史的退役主机
  保留全部历史行与 FK；不提供 `force` 硬删变体。彻底清除历史的需求另立
  裁决（§1.3），**不回落本 ADR**。
- **D4 生命周期与存活正交（心跳联动）**：退役不改写 `status`。被退役主机
  Agent 再心跳（含「按 IP 找回命中退役行」）→ **如实记录**
  （`last_heartbeat`、`status`、版本等照常更新）、**保持退役**、触发**单次
  告警**（去重载体 = **新增持久列**，如 `retire_alerted_at`，与 D1 三列
  同批迁移；`unretire → retire` 重新计轮；可叠加逻辑事件键
  `(host.id, retired_at, event_type)` 保证跨重试/重启幂等；**禁 `Host.extra`
  裸键**）。UI 徽标判据 = `retired_at IS NOT NULL ∧ status = ONLINE`，
  与告警去重解耦。风险面（派发/认领）由 D5 逐点收口；心跳不制造新失败模式。
- **D5 派发/控制面收口（清单面 + 统一判据）**：以「`retired_at IS NULL` ∧
  原判据」为统一判据，覆盖 §2.1 矩阵全部面（允许扩充，不允许缩减）；
  点名共享收口点：`_classify_dispatch_devices_sync`、claim、`iter_plan_run_scan_hosts`、
  `begin_host_upgrade`、`precheck/sync` 两函数、`emit_agent_control` 调用点。
  实现单必须为每个面配回归测试与反例实证（§5）。
  - 派发归位（D-1 裁决）：`host_retired ∈ _FATAL_DISPATCH_REASONS`——
    prepare 400 + 在队 Run 显式 FAILED + 审计；同一 Run 部分主机退役 =
    all-or-nothing；fatal 判据**不得被较早的暂态拒因遮蔽**（分类器先
    device 后 host 的顺序一并修）。
  - 控制面动作分两类（D-5 裁决）：**执行/配置类**（热更新/安装/升级门禁/
    reload/watcher 切换）对退役主机**拒绝**；**数据回收类**（scan/archive/
    日志尾读）**允许但仅显式 admin 触发 + 审计 + `skipped_retired`
    不虚报完整**。Socket.IO 保留连接、只做下行判据（拒绝连接会牵动
    Agent 重连策略）。
- **D5bis 在飞 Run 的判定基准（D-2 裁决）**：派发/认领**活读** `retired_at`；
  已 QUEUED/PRECHECK 的在飞 Run **不静默缩小目标集合**，以显式 `HOST_RETIRED`
  原因收敛（FAILED + 审计）；接受「unretire 使同一 Run 判定翻转」。
  `PlanRunHost` 投影与统计仍按 prepare 冻结。
- **D6 设备与身份**：退役主机的设备行保留（历史锚点），不随退役删除；设备
  物理搬移由现有心跳 re-home 处理（租约阻断语义不变）。
  - 身份契约：**生命周期按主机行 `id` 判定；`ip` 是可变属性**（`PUT
    /hosts/{id}` 改 ip 不改 id）；「同 IP 换机」物理上只能复用原行（ip 唯一
    约束 + 创建 409），**复用是结果、不是判据**；命中优先级契约 = **id 命中
    > IP 命中 > 后缀分配**（`heartbeat.py:196-208`）；仅在 ip 未命中且 id
    冲突时走后缀分配（新行 + 后缀 id，旧行保持退役）——该场景与「同 IP
    换机」不冲突，判据不同。
  - **boot_id/agent_instance_id 的「审计可见」承诺降级**：改为「unretire 与
    身份变化时在 `audit_logs` 记录变更前后快照」；「详情可见当前值」作为
    实现单交付面（HostOut/types.ts 如需展示）；不再声称完整历史可追溯
    （`boot_id` 每次开机随机，`backend/agent/identity.py:21-42`，**换机与
    重启在信号上不可分辨**）。
  - ADR-0035 的 per-host 凭据落地后重审换机注册流程（见 Revisit 2）。
- **D7 显式不做**：不做 `deleted_at` 软删列（见 §3）；不改 FK
  SET NULL/CASCADE；不引入保留期/purge；不做设备退役（独立议题，设备行
  当前无删除面，需求出现时单独提案）。
- **D8 retire ≠ credential revoke**：退役不吊销任何凭据；per-host 凭据
  （ADR-0035 目标形态）落地后，退役主机凭据在有效期内的处置须一并定义
  （吊销/轮换面）。换新（同 IP）运维序列须包含 **SSH host key/凭据** 步骤，
  不只是 unretire。本期不实施凭据动作；Revisit 2 为其触发与替代触发。

### 2.1 D5 收口面矩阵（14 面；`retired_at IS NULL` ∧ 原判据）

| # | 面 | 关键锚点（基线 2026-09-13） |
|---|---|---|
| 1 | 派发快照与准入分类（含 TOCTOU 复检、fatal 归位） | `plan_dispatcher_sync.py:68,88-100,155-168,509-519`；`admission_pump.py:666-676`；`plan_dispatcher_core.py:82-107` |
| 2 | claim（活读、`with_for_update`） | `agent_api.py:396-409` |
| 3 | scan/archive 命令扇出（历史 Run 的 scan_now/archive_now） | `plan_run_scan_scope.py:113-122`；`saq_tasks.py:314-315`；`plan_runs.py:549-566`；`dedup.py:464-477`；`ai_assistant/plan_run_ops.py:391-410` |
| 4 | Socket.IO 房间注册与下行控制（含零校验点） | `socketio_server.py:154-186,749-760`；`dedup.py:508-513` |
| 5 | 统计/容量/metrics（三口径：ORM 谓词 / raw SQL / 泛型 group_by） | `stats.py:264-272,286-298,451-459`；`metrics.py:44-53`；`ai_assistant/tools.py:134-149` |
| 6 | 批量热更新脚本（`--direct` 自选靶） | `backend/scripts/batch_hot_update.py:89-95` |
| 7 | install（现状无任何存活门禁；退役门是**新增**而非对齐） | `hosts.py:793-825` |
| 8 | upgrade-gate（agent 自服务端点带 `abort_running_jobs`；服务层仅存在性） | `agent_api.py:3262-3300,3310-3335`；`host_upgrade_gate.py:229-231` |
| 9 | AI 助手读写面 | `orchestrator.py:670-691`；`ai_assistant/tools.py:374-389` |
| 10 | 设备面（列表/创建归属/前端多选/就绪判定） | `devices.py:98,294-377`；`DeviceMultiSelect.tsx:22-27`；`planExecuteReadiness.ts:165-179` |
| 11 | 预检 SSH 同步与准入 Phase A（脚本校验先于退役过滤，可 SSH 触碰退役机） | `precheck/runner.py:189,216` → `precheck/sync.py:68-104`；`admission_pump.py:497-557,817` |
| 12 | Agent 自服务写面（recovery/sync 覆写 boot_id、心跳设备 re-home、远程日志、脚本目录重拉） | `agent_api.py:2887-2910`；`heartbeat.py:403`；`logs.py:244-262`；`routes/scripts.py:212,231,241` |
| 13 | 管理写路径（watcher-admin-state / dead-letter replay 等） | `hosts.py:557-582,931-1030` |
| 14 | 前端判定与共享缓存 | `planExecuteReadiness.ts:165-179`；`bulkHotUpdate.ts:51-70`；`hostKeys.list()` 三页共享；`fetchHostList` limit=200 |

> 矩阵由评审 synthesis §2 并入（去重后的权威清单）；实现单不得以「缩表」方式
> 缩减覆盖面，允许按新发现扩充并回填本表。

## 3. 备选与否决

- **复用 `status=RETIRED` 枚举值**：否决——`status` owner 是心跳，下一次
  心跳会把 RETIRED 改写回 ONLINE，生命周期被在线机器静默撤销；且 OFFLINE
  （不可达）与退役（不再使用）语义不同。
- **`deleted_at` 软删列**：否决——命名暗示「待删队列/将来可 purge」，与
  「退役是历史终态」矛盾。
- **带历史 force 硬删（双重确认）**：否决——与 #796/#937 保历史决策冲突；
  审计与二次确认无法弥补历史不可回灌。
- **导出归档包后再硬删**：否决——需单独设计导出/回灌/一致性；且「导出后
  删源」仍不满足可追溯。
- **FK 改 SET NULL**：否决——历史行 host 归属丢失，正是 #796 要保护的对象。
- **心跳 409 fail-closed**：本轮否决——心跳承载 backpressure/版本门禁响应，
  拒绝会给 Agent 制造新失败模式；风险面已由 D5 收口。保留为 Revisit 1。
- **心跳自动复活（自动解除退役）**：否决——退役不 sticky。
- **派发归 retryable + 停止条件**（D-1）：否决——退役永不自愈，可重试排队
  无上界、无 dead-letter、无告警；与「退役 = 配置事实」语义不符。取 fatal。
- **在飞 Run 沿用冻结快照**（D-2）：否决——`watcher_admin_active` 的冻结
  先例会让已 prepare 的 Run 继续派发到退役机，违背 D5 字面；取「活读 +
  显式 `HOST_RETIRED` 收敛」。
- **严格 OFFLINE/心跳超时前置**（D-3）：否决——要求先停 Agent 存在
  「停 Agent ↔ 新派发」竞态与最长 300s 判定期；取 Cordon（status 不进前置）。
- **`Host.extra` 承载去重戳**（D-4）：否决——主心跳每拍重建 extra，裸键必被
  抹除、每拍重响；取新增持久列。
- **数据回收类一律拒绝**（D-5）：否决——退役前最后一批设备日志将无法回收；
  取「执行/配置类拒绝 + 回收类显式 admin + 审计 + `skipped_retired`」。

## 4. 影响与不变量

- **不变量 1（拆两条，v0.2）**：
  - 可派发 = `retired_at IS NULL` ∧ `status != OFFLINE` ∧ ¬维护窗；
  - 可认领 = `retired_at IS NULL` ∧ `status = ONLINE`。
  两式**已注明与现状一致**（退役引入前 DEGRADED 即「可派发、不可认领」，
  属未声明的既有容量行为；D5 不改变它，只叠加退役判据）。
- **不变量 2**：retire/unretire 不改写任何历史行（job/device/plan_run_host/
  lease 及全部 FK 原样）；`DELETE` 预检不变。
- **不变量 3（重写）**：retire/unretire 必须审计（who/when/reason，
  `retire_reason` 必填；审计失败阻断本操作）；退役主机心跳告警按主机去重
  ——载体为 D4 新增持久列，**同一次退役周期内不重置**；OFFLINE→ONLINE
  震荡是否二次提醒随载体选型写明；`unretire → retire` 重新计轮。
- **迁移**：三列（D1）+ 去重列（D4）**同批** additive nullable，无回填。
  - **发布顺序**：迁移 → 全部消费方支持退役（D5 矩阵）→ 才开放
    retire/unretire 入口（避免入口先于过滤面开放）；
  - **回滚 = 有意数据丢失面**：`retired_at` 是唯一生命周期真源，「drop 三列」
    等于一次性、无审计地解除全 fleet 退役——schema 可降级 ≠ 运维决定不被
    静默撤销。存在退役行时**禁止无条件删列**；缓解 = retire/unretire 强制
    审计（`audit_logs` 独立表，回滚不动它 ⇒ 退役事实可重放）+
    downgrade 守卫/往返测试 + 注释「drop 会丢失退役状态」；
  - **配套清单**（同表先例 `m8n9o0p1q2r3_host_maintenance_window.py`）：
    ORM ↔ alembic ↔ Pydantic（HostOut/retire 请求）↔ 前端 `types.ts` 四端
    配对；`check_schema_sync` 对齐（**禁 `--rebaseline`**、不动 baseline、
    显式命名约束/索引、`sa.DateTime(timezone=True)` 拼写钉死、
    `retired_by` 用 `String(128)`）；单 head（届时 `alembic heads` 实读）；
    **本次不新增索引**；downgrade 往返测试（CI 不跑 downgrade）。
- **运维代价**：退役**不再要求先停 Agent**（Cordon）；同 IP 换机需 unretire
  一步 + SSH host key/凭据处理（D8）；活体退役主机需人工处置（告警提示）。
- **前端面（v0.2 更正）**：`api.hosts.delete`（`hosts.ts:24-25`）**有**调用点
  ——单删 `HostsPage.tsx:107`、批删 `:308`；两个入口正是 D2 要接续的界面，
  实现单须显式决定其与 retire/unretire 的关系（含批量流不得吞 409 文案）。
  `include_retired` 需穿透全站共享取数 `fetchHostList`（`hosts.ts:33`，
  limit=200 静默截断）与 `hostKeys.list()=['hosts']` 三页共享缓存
  （HostsPage/PlanExecutePage/DevicesPage）；前端判定面
  `planExecuteReadiness.ts:165-179` / `bulkHotUpdate.ts:51-70` 只认 ONLINE，
  须同步改为叠加退役判据。

## 5. 验收与 Revisit

- **验收（实现单另开，ADR Accepted ≠ 实施启动）**：
  1. `POST /hosts/{id}/retire` / `unretire` 可用：reason 必填、前置 409
     （Cordon）、幂等、审计齐全（含 fail-closed）；
  2. D5 矩阵（14 面）逐点回归测试 + **反例实证留痕**（临时移除过滤点 →
     用例转红；命令与结果记入 PR/Agent Note）；**逐点 mutation 防多层防护
     互相遮蔽**；Python 侧 mutation 须清 `__pycache__`（本仓先例：sed+cp
     复用旧字节码假绿）；统计面断言**排除退役后的具体计数值**（泛型
     group_by/raw SQL 删特例仍会输出看似正常的数值）；给出「收口点 ↔ 测试
     文件」映射；
  3. 派发 fatal 与在飞 Run 语义（D-1/D-2）：prepare 400、在队 Run
     FAILED+审计、all-or-nothing，各有测试；
  4. 活体退役心跳：保持退役 + 徽标 + **单次告警四条边沿断言**（首拍 1 条 /
     持续 N 拍仍 1 条 / 超时恢复再 1 条 / unretire→retire 重新计轮），
     「震荡」定义随 D-4 载体写明；
  5. #937 的 DELETE 预检无回归（现有用例保持全绿）。
- **Revisit**：
  1. 若观察窗口内「活体退役主机」长期残留或告警被证明无效，升级为心跳
     409 拒绝（Agent 侧失败模式需同步评估）；
  2. ADR-0035 per-host 凭据落地时：定义退役主机凭据的吊销/轮换面
     （retire ≠ credential revoke），并重审同 IP 换机注册流程；若 ADR-0035
     触发条件长期未触发，由本 ADR 主动复核替代触发（不长期挂起）；
  3. 若出现高频「退役后彻底清除」需求，**另立裁决** purge 机制（现行清理
     是 `run_retention_cleanup`；ADR-0025 只管日志文件闭环），不回落
     force 硬删；
  4. 设备退役/清理入口出现真实需求时独立提案（本 ADR 只保证设备不因退役
     被删）；
  5. 若后续要求把「boot_id/agent_instance_id 可见」升级为交付面，按
     HostOut + `types.ts` + 审计事件一并兑现（R6-(b)）。

## 6. v0.2 修订记录（评审 R1–R19 / 裁决 D-1～D-6 → 落点）

| 来源 | 修订 |
|---|---|
| R1 / D-1 | §2 D5：`host_retired` 归 fatal + all-or-nothing + 判据遮蔽修正；§3 增否决「retryable + 停止条件」；§5 验收 3 |
| R2 / D-4 | §2 D4：去重载体改新持久列（禁 `Host.extra` 裸键）；§4 不变量 3 重写；§3 增否决「extra 承载」 |
| R3 / D-5 | §2 D5：五面 → 14 面矩阵 + 统一判据 + 共享收口点；回收类分类放行；§4 前端面 |
| R4 | §1.2/D4：心跳锚点改指 `heartbeat.py:196-236`，声明轻量端点归属；「IP 找回命中退役行」视为活体退役 |
| R5 | §1.2/D6：身份契约（id 判定、ip 可变、复用是结果、命中优先级、后缀边界可达） |
| R6 | §2 D6：boot_id/agent_instance_id 承诺降级为审计快照 + 当前值交付面 |
| R7 / D-2 | §2 D5bis：活读 + 显式 `HOST_RETIRED` 收敛；§3 增否决「冻结快照」 |
| R8 / D-3 | §2 D2：Cordon 前置（status 不进前置、含 QUEUED 引用、显式不检查租约、同行锁复检）；§3 增否决「严格门」 |
| R9 | §4 不变量 1 拆两条；§1.2 补 DEGRADED latent 注记 |
| R10 / R13 | §4 迁移：发布顺序、回滚=有意数据丢失、配套清单与门禁细节 |
| R11 / D-6 | §2 D1：三列全留 + reason 必填 + `audit_logs` 真源 + 审计 fail-closed + unretire 写回语义 |
| R12 / D8 | §2 新增 D8（retire ≠ credential revoke、换新序列含 SSH host key）；Revisit 2 补替代触发 |
| R14 | §4 前端面：`api.hosts.delete` 双错更正 + `include_retired` 穿透 + 共享缓存/判定面 |
| R15 | §1.1：「永久 409」更正为设备维度无界、Job/PlanRunHost 有界（3 天保留期） |
| R16 | §1.3/D3/Revisit 3：ADR-0025 死引用移除，改为「另立裁决」 |
| R17 | §5 验收 2/3：反例实证留痕、逐点 mutation、`__pycache__`、统计面具体计数、告警四条边沿断言 |
| R18 | 心跳超时双默认值已被单源化修复合销（`job_timeout_config.py:86`），本版不引用该论据 |
| R19 | 择要吸收：术语限定（「主机退役」vs 脚本版本退役）、claim 拒绝信号、docs/DOC-MAP/README 登记（含 synthesis 与 errata 链接） |

> 评审模型归属（会话 × 模型）见 errata：`docs/reviews/REVIEW_ADR0038_2026-09-13_571d95-attribution.md`。
