# ADR-0021: 脚本内容对齐门禁、独立 Abort API 与热更新软禁

- 状态：Proposed
- 优先级：P0
- 目标里程碑：M3.1
- 日期：2026-05-07
- 决策者：平台研发组
- 标签：派发门禁, sha256, abort, 热更新, SocketIO RPC, PlanRun

## 背景

ADR-0020 完成 Plan / PlanStep 切换后，编排定义、Job 派发、Agent 执行三者基本对齐：`PlanRun.plan_snapshot` 已内嵌每个 step 的 `script_name` / `script_version` / `nfs_path` / `content_sha256`，Agent 在执行 step 前通过 `ScriptRegistry` 解析脚本元数据。

但脚本**内容一致性**依然是裸露的工程债：

1. **DB 与 Agent 文件可能漂移**：
   - Agent 侧脚本目录（`/opt/stability-test-agent/scripts`）由 host hot-update 机制 rsync 写入；
   - 后端 `Script.content_sha256` 在 `POST /api/v1/scripts/scan` 时从 `STP_SCRIPT_ROOT` 计算；
   - 漂移来源：历史 Agent 部署时间早于某次 scan、手工修改、部分 host 漏更新、热更新失败但未告警。

2. **运行时才发现漂移已经太晚**：
   - 当前派发只校验 Plan 定义有效，不校验 Agent 端脚本是否就绪；
   - 漂移导致的失败被当作普通 step 失败计入 PlanRun 失败率，污染失败统计；
   - 排查链路长（运维拿到 ABORTED Job 才往回查 nfs_path → host 路径 → sha 不一致 → 重新部署）。

3. **缺少主动 abort 通道**：
   - Agent 端 `pipeline_engine` 已具备完整 abort 协议（`_is_aborted` 回调 + lease 409 → 干净退出 → 状态 ABORTED）；
   - 但平台没有运维主动入口，唯一触发方式是 reconciler 兜底失效租约或外部 SQL 释放 lease；
   - 后果：误派发、Plan 配置错、设备异常时只能等 Plan 自然失败或人工动 DB。

4. **热更新破坏运行中 Job**：
   - `POST /api/v1/hosts/{id}/hot-update` 当前对 host 上是否有 RUNNING Job 不做检查；
   - rsync 期间 Agent systemd 重启 → `pipeline_engine` 进程被 SIGKILL → Job 被 reconciler 标 FAILED + lease 释放，状态机不诚实（"被中断"被记成"失败"）。

## 决策

### D1 — 派发门禁（Dispatch Gate）

将 `POST /api/v1/plans/{id}/run`（MANUAL 路径）由"同步派发"改为"异步入队 + 立即返回 PlanRun 行"：

- 入队前**立即创建 PlanRun**（`status='RUNNING'`、`run_context.precheck.phase='verifying'`），`plan_snapshot` 一次性写入。前端即刻可以跳转 PlanRun 详情页观察门禁进度。
- **派发门禁仅作用于 MANUAL 路径**。SCHEDULE（cron 触发）和 CHAIN（PlanRun 链路触发）维持现有同步派发，不经门禁。后续若需扩展由独立 ADR 决议。
- 入队 SAQ task `precheck_and_dispatch(plan_run_id)`，同步阻塞返回 `{plan_run_id, ...}` 给前端。
- task 内部按以下阶段推进，每个阶段把进展写入 `run_context.precheck`：
  1. **verify** — 对涉及的每个 host 通过 SocketIO RPC 取得 Agent 实测 sha256，与 `plan_snapshot.script_meta` 比对。
  2. **sync**（仅当 verify 失败时）— 对每个失败 host 触发 hot-update（rsync + Agent restart）。
  3. **re-verify** — 同步后再次 verify。
  4. **dispatch** — 全部对齐后创建 JobInstance 行，PlanRun 进入"对齐完成"状态（仍为 status='RUNNING'，`run_context.precheck.phase='ready'`）。

PlanRun 列表语义：所有派发尝试（成功 / 失败）都在列表中可见，便于审计。

### D2 — 不扩展 PlanRunStatus 枚举

不引入 `PRECHECK` / `SYNCING` / `PRECHECK_FAILED` 等新状态。原因：

- 状态机膨胀会蔓延到 aggregator、recycler、前端筛选等所有判断点；
- `run_context.precheck` 已经有结构化字段表达进度，前端展示足够；
- 终态保持既有四种：`SUCCESS` / `PARTIAL_SUCCESS` / `FAILED` / `DEGRADED`；
- 派发门禁失败 → `status='FAILED'` + `result_summary.precheck_failed=true`，与"测试运行失败"语义对偶，不混淆。

### D3 — `run_context.precheck` JSON Schema

```jsonc
{
  "precheck": {
    "phase": "verifying" | "syncing" | "ready" | "failed",
    "started_at": "2026-05-07T10:00:00Z",
    "completed_at": "2026-05-07T10:01:30Z",
    "hosts": {
      "<host_id>": {
        "status": "ok" | "syncing" | "synced" | "failed",
        "checked_at": "2026-05-07T10:00:02Z",
        "synced_at": "2026-05-07T10:00:35Z",
        "scripts": [
          {
            "name": "monkey_launch",
            "version": "v2.0.0",
            "expected_sha": "ab12...",
            "actual_sha": "ab12...",
            "ok": true,
            "exists": true
          }
        ],
        "sync_attempts": 0,
        "error": null
      }
    },
    "final_result": null | "ready" | "failed" | "aborted",
    "errors": []
  }
}
```

`run_context` 列已是 JSONB，不需要 Alembic。

### D4 — 平台 DB 是脚本内容唯一权威

- Agent 不与 Git 比对、不与本地 SQLite 比对、不与 NFS 缓存比对；
- 验证方向：Agent 计算磁盘文件 sha256，回报后端，后端用 `plan_snapshot.script_meta[*].content_sha256` 对账；
- DB sha 写入路径：`POST /api/v1/scripts/scan`（运维显式触发）、`POST /api/v1/scripts/{name}/versions`（新建版本时写入）。其他途径不允许修改 sha。

### D5 — 同步通道 = host hot-update

不引入新通道。Agent 端脚本目录由 host hot-update（rsync + Agent restart）维护。

派发门禁的 sync 阶段**调用同一个 hot-update 函数**（`backend.services.host_updater.execute_hot_update`），区别仅在于：

- 调用方为 SAQ task（`username='system:precheck'`）而非运维（`username=current_user.username`）；
- audit log 通过用户名区分两种来源；
- 不对 hot-update 函数加 `trigger_source` 参数——保持函数签名稳定。

### D6 — Multi-Agent 全有或全无

任何一个 host 的 verify 或 sync 失败 → 整个 PlanRun 标 FAILED，不部分降级。原因：

- Plan 失败阈值（`failure_threshold`）是基于 step 失败率的，不能与"派发未对齐"混算；
- 部分对齐会让一半设备跑某版本脚本、另一半跑另一版本，结果不可比；
- 全有或全无让运维只需排查"哪台 host 没对齐"，路径短。

### D7 — 独立 Abort API

新增 `POST /api/v1/plan-runs/{id}/abort`：

- **PlanRun 状态分支**：
  - `phase='verifying' | 'syncing'`：直接把 PlanRun 标 `FAILED` + `run_context.precheck.final_result='aborted'`，无 Job 需要清理；
  - `status='RUNNING' & phase='ready'`（已派发）：
    - PENDING Job → 直接标 `ABORTED`；
    - RUNNING Job → 释放 `device_lease`，由 Agent LeaseRenewer 下次续约（≤10s）拿到 409，`pipeline_engine` 收尾当前 step 干净退出，状态 → `ABORTED`；
    - 由 reconciler 15s 间隔兜底，若 Agent 不响应则强制标 `ABORTED`。
- **同步语义**：API 立即返回（不阻塞等待 Agent 收尾），前端通过 SocketIO 实时刷新；
- **审计**：每次 abort 写 `audit_log(action='abort_plan_run')`，details 含 `reason`、`affected_jobs`。

abort 不只是为热更新服务——它是产品独立价值的运维功能。

### D8 — 热更新软禁（Hard-lock with Abort-then-update）

`POST /api/v1/hosts/{id}/hot-update` 行为：

| 触发场景 | 当前 host 上活跃 Job | 行为 |
|---|---|---|
| 无活跃 Job | 0 | 直接执行 hot-update（既有逻辑） |
| 有活跃 Job + 默认调用 | >0 | **返回 409** + `detail` 列出 active_jobs |
| 有活跃 Job + `abort_running_jobs=true` | >0 | 串联：① abort 所有活跃 Job → ② 等到 ABORTED（poll, timeout=45s）→ ③ 执行 hot-update |
| abort 超时 | >0（45s 内仍有 Job 未 ABORTED） | **返回 504**，运维介入 |

**关键不变量**：hot-update 函数本身不会主动 SIGKILL Agent 上正在执行的 step。Job 必须先走完正常 abort 协议（释放 lease → Agent 收尾 → ABORTED）才允许 rsync。这保证 Job 状态机诚实。

`GET /api/v1/hosts/{id}` 出参增加：

```jsonc
{
  "active_job_count": 2,
  "active_jobs": [
    {"id": 123, "plan_run_id": 42, "device_id": 1, "stage": "patrol"},
    {"id": 124, "plan_run_id": 42, "device_id": 2, "stage": "init"}
  ]
}
```

让前端能展示明细、生成二次确认对话框。

### D9 — Per-PlanRun verify-once（运行时不重复校验）

派发门禁的 sha 校验**仅在派发瞬间做一次**，运行中不再重复。原因：

- `plan_snapshot.script_meta[*].nfs_path` 已经在 PlanRun 创建时锁定；
- Agent 收到 Job 后按 plan_snapshot 的 nfs_path 直接 spawn，已经隐含锁定；
- 运行中即使 host 被热更新，已下发到 Agent 的 Job 仍按 plan_snapshot 路径运行（pipeline_engine 已绑定 subprocess）；
- 每 step 校验 sha 会引入 N×M 次磁盘读 + 网络往返，性能浪费。

热更新软禁（D8）保证运行中不能 hot-update，进一步消除"中途漂移"的可能性。

### D10 — Agent SocketIO RPC：verify_scripts

复用现有 `/agent` namespace + `agent:{host_id}` room：

- 后端 `AgentNamespace` 维护 `host_id → sid` 字典（在 `on_connect` / `on_disconnect` 维护）；
- 后端导出 helper `await call_agent(host_id, event, data, *, timeout=10.0)`，内部调 `sio.call(event, data, to=sid, namespace='/agent', timeout=timeout)`；
- Agent 端 `ws_client` 注册同步 handler，return 值自动 ack 回传给后端。

**RPC 协议**：

请求（Server → Agent）：

```jsonc
{
  "event": "verify_scripts",
  "data": {
    "expected": [
      {
        "name": "monkey_launch",
        "version": "v2.0.0",
        "nfs_path": "/opt/stability-test-agent/scripts/monkey_launch/v2.0.0/monkey_launch.py",
        "sha256": "ab12cd34..."
      }
    ]
  }
}
```

响应（Agent → Server, ack）：

```jsonc
{
  "host_id": "host-101",
  "agent_version": "ADR-0020-rev2",
  "results": [
    {
      "name": "monkey_launch",
      "version": "v2.0.0",
      "expected_sha": "ab12cd34...",
      "actual_sha": "ab12cd34...",
      "ok": true,
      "exists": true,
      "error": null
    }
  ],
  "checked_at": "2026-05-07T10:00:02Z"
}
```

**异常处理**：

- Agent 离线 → `call_agent` 抛 `AgentNotConnectedError` → 该 host 标 `failed` + `error='agent_offline'`，触发 sync（hot-update 自带 SSH，与 SocketIO 在线状态正交）；
- RPC 超时（默认 10s）→ 标 `failed` + `error='verify_timeout'`，进入 sync；
- Agent 文件读取异常 → response.results[*].error 填入 errno，ok=false。

## 实施切片

| Commit | 范围 | 单测 |
|---|---|---|
| **C1** | Agent SocketIO `verify_scripts` RPC：`AgentNamespace.host_to_sid` 维护 + `call_agent()` helper + Agent 端 handler + `_hash_local_script_file` 工具 | 4 cases：正常 / 文件缺失 / sha 不一致 / Agent 离线 |
| **C2** | `run_context.precheck` Pydantic schema + `PlanRunOut` 出参 | 1 case：序列化往返 |
| **C3** | SAQ task `precheck_and_dispatch` + `/plans/{id}/run` 改异步入队 + `PlanRun` 立即创建 | 6 cases：无漂移派发 / 单 host 漂移 sync 通过 / 多 host 漂移其一失败 / Agent 全离线 / sync 后 re-verify 仍失败 / 入队幂等 |
| **C4** | `POST /plan-runs/{id}/abort` + `GET /hosts/{id}` 增 `active_jobs` + `/hot-update?abort_running_jobs=true` 复合 | 8 cases：abort 三种 phase / abort 已终态 PlanRun（409）/ hot-update 无 Job 直通 / hot-update 有 Job 默认 409 / abort+update 串联 / abort 超时 504 |
| **C5a₁** | ADR-0022 patrol 心跳聚合 + 失败退避 + 手动干预（独立 ADR） | 见 ADR-0022 测试矩阵 |
| **C5a₂** | 5 个 PlanRun 聚合端点：`GET /plan-runs/{id}/{chain,timeline,events,devices,watcher-summary}` + `step_trace` 复合索引（`idx_step_trace_job_stage` / `idx_step_trace_job_status_ts`，alembic `e8f9a0b1c2d3`）+ Prometheus 指标埋点（PlanRun terminal / dispatch_gate / patrol heartbeat / log_signal / manual action） | 15 cases：chain 三节点 + 404 / timeline stage 聚合 + 404 / events 多源融合 + stage/severity 过滤 + 分页 / devices facet + status/host 过滤 + backoff 派生 / watcher-summary trend + 422 + 404 |
| **C5b** | 前端 `PlanRunDetailPage` 骨架：`PlanRunTopbar`（status pill + 实时 run time + 中止确认弹窗）/ `PlanChainBreadcrumb`（紧凑单行 Plan 链 + block_reason hover）/ `DispatchGateCard`（precheck phase + hosts × scripts × sha256 矩阵 + sync 进度）/ `BusinessFlowTimeline`（双栏:左纵向阶段 stepper + 右事件流，含 stage/severity 过滤 + facet 计数）。路由 `/execution/plan-runs/:runId` 切换到 `PlanRunDetailPage`，旧 `PlanRunMatrixPage` 降级到 `/execution/plan-runs/:runId/matrix`。`api.planRuns.{getChain,getTimeline,getEvents,getDevices,getWatcherSummary,abort,manualRetryJob,manualExitJob}` API 客户端 + `PrecheckState` 等 5 端点 TS 类型补齐。 | Vitest 12 cases：`PlanChainBreadcrumb`（current 高亮 / pending block 不可点 / loading & empty）+ `BusinessFlowTimeline`（stage 渲染 / 事件渲染 / filter 提升 / facet 计数 / 空态）+ `PlanRunDetailPage`（4 块布局拼接 / 中止确认弹窗 → POST `/abort` / 返回列表导航 / precheck 缺失时不渲染派发门禁卡片 + 终态隐藏中止按钮） |
| **C5c** | 前端 `DeviceMatrixCard`（表格/缩略图双视图 + by_status 6 色 chip + by_host 下拉 + 退避连击红字 + manual_action 标记）/ `DeviceDetailDrawer`（KV 完整字段 + 「立即重试 / 退出该设备」AlertDialog 二次确认 + 跳转 Job 报告）/ `WatcherSummaryCard`（4 时间窗 chip + category list 含 trend 箭头/+δ-δ + 阈值 banner + 进度条 + threshold marker）三组件落地。`PlanRunDetailPage` 替换两个 placeholder + 接入 `useSocketIO('/ws/plan-runs/{id}')`：`JOB_STATUS` → invalidate devices+timeline+events；`PLAN_RUN_STATUS` → invalidate run+chain+timeline+devices；`WATCHER_SIGNAL` → invalidate watcher+events（事件**只作 invalidation hint**，不在前端 patch 缓存，refetch 解出权威态）。后端补 `broadcast_watcher_signal()` + `_emit_job_status_invalidation()`；`ingest_log_signals` 仅对 ON CONFLICT 实际入库的行推送（冲突丢弃不推），sync 的 `manual_retry_job` / `manual_exit_job` 走 `schedule_emit` 桥接异步推送。`useSocketIO` `EVENTS` 和 `parseWsUrl('/ws/plan-runs/{id}')` 加入 `watcher_signal`。 | Vitest 12 cases：`DeviceMatrixCard`（5：表格渲染 + 视图切换 + 过滤提升 + 选中回调 + 空态）+ `WatcherSummaryCard`（4：trend + threshold banner + 警告 banner + 空态 + 窗口切换）+ `PlanRunDetailPage`（3 新增：DeviceMatrix/Watcher 渲染 + 抽屉重试 confirm 流转 + JOB_STATUS/PLAN_RUN_STATUS/WATCHER_SIGNAL 三种 SocketIO 推送的精确 invalidation 范围）。pytest 3 cases：`test_log_signals_broadcasts_watcher_signal_per_inserted_row`（PG-only：每条入库 1 推 + 冲突 0 推）+ `test_manual_retry_emits_job_status_to_plan_run_room` + `test_manual_exit_emits_job_status_to_plan_run_room`（patch `schedule_emit` 验证 event/room/payload）。 |
| **C6** | 前端 host 管理热更新流程升级——`HostHotUpdateConfirmDialog` 新组件接管原 `useConfirm` 简单确认：打开时拉 `GET /hosts/{id}` 拿权威 `active_jobs` 快照，渲染分支：① `active_job_count == 0` 绿色 ShieldCheck banner + 确认按钮直接可点（`abort_running_jobs=false`）；② `>0` 琥珀 banner 显示 active count + 受影响 Job 列表（id / device / plan_run / status / started_at），并要求用户勾选红色「我已知悉」复选框才能解锁红色「中止 Job 并热更新」确认按钮（`abort_running_jobs=true`），未勾选时按钮显示「需先勾选确认」并 disabled。`hotUpdate.trigger(hostId, { abortRunningJobs })` 拼 `?abort_running_jobs=true` query 参数。`HostsPage` 用 `pendingHotUpdateHostId` state 替代 `useConfirm` 流程，mutation 完成后 invalidate `['hosts'] / ['host-detail', hostId] / ['active-jobs']`；遇到 409 + `detail.active_jobs` 时回退打开 dialog 引导用户走 abort 路径。`api.hosts.getDetail(id)` 新增 unwrap 端点；`Host` 类型补 `active_jobs?: HostActiveJob[]` / `active_job_count?: number`。 | Vitest 6 cases：`HostHotUpdateConfirmDialog`（① null hostId 不渲染 ② active=0 绿 banner + 直接 enabled + 调用 `abortRunningJobs:false` ③ active=2 默认 disabled + 列出受影响 Job + 勾选后 enabled 调用 `abortRunningJobs:true` ④ 加载中 confirm disabled + skeleton ⑤ 取消不触发 onConfirm ⑥ 切换到不同 host 重置勾选）+ HostsPage 集成回归 5 cases 全绿。 |

后端 C1~C4 + C5a₁~C5a₂ 串行 commit 全部完成 → C5b 落地 → C5c / C6 串行收尾。

## 后果

### 收益

- **派发即对齐**：Job 启动前已确认 sha 一致，杜绝"脚本缺失/过时"导致的运行失败；
- **失败语义诚实**：`result_summary.precheck_failed` 与 step 失败率独立统计；
- **运维主动止血**：误派发、配置错可一键 abort；
- **热更新可控**：与运行中 Job 严格互斥，状态机不再有"被 SIGKILL"的灰色路径；
- **审计完整**：派发门禁的每个 host × 每个脚本的对账记录写入 `run_context.precheck`，abort / hot-update 写 `audit_log`。

### 代价

- **派发耗时增加**：verify ~1s（每 host 一次 SocketIO RPC），必要时 sync ~30s（rsync + Agent restart），re-verify ~1s。无漂移路径影响 ~2s；
- **运维想热更新有运行 Job 时被卡住**：必须显式选择"中止 Job 并热更新"或等 Job 自然结束。这是设计目的；
- **新增 SocketIO 双向 RPC 路径**：增加一处 host_id↔sid 映射维护点，加一类网络异常需处理。

### 备选方案被排除

| 方案 | 排除理由 |
|---|---|
| Agent 启动后周期性 verify | 拉模型，无法保证"派发瞬间对齐"；漂移检测有窗口期 |
| Step 级 verify | 性能浪费（N×M 次校验）；且 D9 已通过 plan_snapshot 锁定 nfs_path 防御中途漂移 |
| 扩展 PlanRunStatus 枚举（PRECHECK / SYNCING / PRECHECK_FAILED） | 状态机膨胀蔓延到 aggregator / recycler / 前端筛选；`run_context.precheck` 已能表达 |
| 派发失败回滚 PlanRun 行 | 失败案例不可复盘；保留 `status='FAILED' + precheck_failed=true` 让排查闭环 |
| Hot-update 软禁（force 可绕过） | 强制路径会 SIGKILL 运行中 Job，状态被记成 FAILED 而非 ABORTED，状态机不诚实 |
| Hot-update 拒绝即彻底拒绝（不提供 abort+update 串联） | 运维改完脚本想立刻部署但发现 host 上有 Job → 只能等几小时，体验差 |

## 引用 / 关联

- ADR-0018 — Watcher 子系统主线（Job 终态聚合复用）
- ADR-0019 — Device Lease v2（abort 协议依赖 lease 释放）
- ADR-0020 — Plan-Step 一次性切换（plan_snapshot.script_meta 作为权威）
- `backend/services/host_updater.py` — hot-update 既有实现
- `backend/realtime/socketio_server.py` — SocketIO 服务端，本 ADR 在此扩展 RPC 能力
- `backend/agent/ws_client.py` — Agent SocketIO 客户端，本 ADR 在此注册 verify_scripts handler
- `backend/agent/pipeline_engine.py` — abort 协议实现已就绪，本 ADR 仅暴露 API
