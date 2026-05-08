# stability-test-platform - 稳定性测试管理平台

[根目录](../CLAUDE.md) > **stability-test-platform**

---

## 变更记录 (Changelog)

### 2026-05-08 — ADR-0021 C6 — 主机热更新二次确认 + 中止运行中 Job 一键复合
- **新增前端组件** `frontend/src/components/host/HostHotUpdateConfirmDialog.tsx`:
  - 打开时按需 `GET /api/v1/hosts/{id}` 获取**权威** `active_jobs` 快照(列表端点不返回此字段);
  - `active_job_count == 0` 渲染绿色 ShieldCheck banner,确认按钮直接可用 → 等价旧路径;
  - `> 0` 渲染琥珀 banner + 受影响 Job 列表 (`#id` / `Device #x` / `PlanRun #y` / `RUNNING|PENDING` / `started_at`),并要求用户**手动勾选**红色「我已知悉」复选框才能解锁红色「中止 Job 并热更新」按钮;
  - 未勾选时按钮 disabled 并显示「需先勾选确认」,杜绝误操作;
  - 切换 host 时自动重置勾选,加载中显示 skeleton + cancel 始终可用。
- **API client 扩展** `frontend/src/utils/api/hosts.ts`:
  - 新增 `hosts.getDetail(id)` (unwrap 端点) — `useQuery` 友好;
  - `hotUpdate.trigger(hostId, { abortRunningJobs })` 拼接 `?abort_running_jobs=true` query 参数,触发后端 abort → 等 Agent 自然退出 (≤45s) → 热更新串联流程;
  - 新增 `HotUpdateResult` 类型暴露 `aborted.{plan_runs, aborted_jobs, drained_lingering_jobs}` 字段。
- **类型扩展** `frontend/src/utils/api/types.ts`:
  - 新增 `HostActiveJob` 接口 (id / plan_run_id / plan_id / device_id / status / started_at);
  - `Host` 增补 `active_jobs?: HostActiveJob[]` + `active_job_count?: number`(仅在 detail 端点 populated)。
- **HostsPage 集成** `frontend/src/pages/hosts/HostsPage.tsx`:
  - `pendingHotUpdateHostId` state 替代旧 `useConfirm` 一行确认;
  - mutation 完成后 invalidate `['hosts'] / ['host-detail', hostId] / ['active-jobs']` 三个 queryKey,确保表格 + dialog + 全局活跃 Job 计数同步刷新;
  - 防御性 409 fallback:若调用绕过 dialog 直接 trigger 且后端返回 `409 + detail.active_jobs`,自动重新打开 dialog 引导用户走 abort 路径;
  - mutation 进行中禁止关闭 dialog,避免误关丢失上下文。
- **测试覆盖**:
  - `HostHotUpdateConfirmDialog.test.tsx` 新增 6 cases,覆盖空 hostId / active=0 直确认 / active>0 强制勾选 / 加载中 disabled / 取消不触发 / host 切换重置勾选;
  - HostsPage 现有 5 cases 全部回归通过;
  - 全量 frontend `npx vitest run` 19 文件 / 109 cases 全绿,`npx tsc --noEmit` 通过。
- **ADR-0021 实施切片**:C6 行更新为完整实施细节,标注 6 cases Vitest + HostsPage 5 case 回归。
- 依赖:无新增

### 2026-05-08 — ADR-0021 C5c — 设备总览 + Watcher 聚合 + SocketIO 增量推送
- **3 个新组件**(`frontend/src/components/plan-run/`):
  - `DeviceMatrixCard` — 表格 / 缩略图双视图;status/host facet 过滤;BACKOFF / RISK 状态色;失败连击 ≥3 红字加粗;`manual_action=EXIT_REQUESTED` 显示「退出待执行」
  - `DeviceDetailDrawer` — 完整 KV 字段 + 「立即重试」/「退出该设备」按钮(AlertDialog 二次确认 + reason 描述);终态 / 已请求时按钮 disabled;支持跳转 Job 报告
  - `WatcherSummaryCard` — 4 个时间窗 chip(15min/1h/6h/24h);category 行(trend ↑↓ 箭头 + 颜色 + 影响设备数 + latest serial);超阈值红色 banner / 未超但有信号 amber banner;底部 abnormal_rate 进度条 + threshold marker
- **PlanRunDetailPage 收口**:替换 2 个 C5b placeholder;接入 `useSocketIO('/ws/plan-runs/{id}')`:
  - `JOB_STATUS` 推送 → invalidate `devices` + `timeline` + `events`
  - `PLAN_RUN_STATUS` 推送 → invalidate `run` + `chain` + `timeline` + `devices`
  - `WATCHER_SIGNAL` 推送 → invalidate `watcher-summary` + `events`
  - 推送只作"invalidation hint",refetch 解出权威态(避免前端缓存与服务端漂移)
- **后端推送扩展**(`backend/realtime/socketio_server.py`):
  - 新 `broadcast_watcher_signal(run_id, *, job_id, device_serial, category, inserted_count)` — 推 `watcher_signal` 事件到 `plan_run:{id}` room
  - `ingest_log_signals` 仅对 ON CONFLICT 实际入库的行推送(冲突丢弃不推);通过 `select(JobInstance.id, plan_run_id)` 反查 PlanRun 路由
  - 新 helper `_emit_job_status_invalidation` (`backend/api/routes/plan_runs.py`) — sync 的 `manual_retry_job` / `manual_exit_job` 通过 `schedule_emit` 桥接异步推送 `job_status` 到 plan_run room
- **`useSocketIO` 扩展**:`EVENTS` 列表加 `watcher_signal`;`parseWsUrl('/ws/plan-runs/{id}')` 解析事件清单同时包含 `job_status` / `plan_run_status` / `watcher_signal` 三类
- **测试**:
  - Vitest 12 新增:`DeviceMatrixCard.test.tsx`(5) + `WatcherSummaryCard.test.tsx`(4) + `PlanRunDetailPage.test.tsx`(+3:DeviceMatrix/Watcher 渲染 + 抽屉重试 confirm 流转 + 3 种 SocketIO 推送的精确 invalidation 范围)
  - pytest 3 新增:`test_log_signals_broadcasts_watcher_signal_per_inserted_row`(PG-only:每条入库 1 推 + 冲突 0 推) + `test_manual_retry_emits_job_status_to_plan_run_room` + `test_manual_exit_emits_job_status_to_plan_run_room`(patch `schedule_emit` 验证 event/room/payload)
- **回归**:全量 frontend `npx vitest run` — **18 文件 / 103 case 全过**;后端 PG 模式 watcher + manual_retry_exit + plan_run_aggregation **31 新 case 全过**(2 个 pre-existing claim_jobs 失败已识别为与 C5c 改动无关)
- 依赖:无新增

### 2026-05-08 — ADR-0021 C5b — PlanRunDetailPage 骨架 + 4 块组件 + Vitest
- **新页面**:`frontend/src/pages/execution/PlanRunDetailPage.tsx` — 路由 `/execution/plan-runs/:runId`,旧 `PlanRunMatrixPage` 降级到 `/execution/plan-runs/:runId/matrix`
- **4 个组件**(`frontend/src/components/plan-run/`):
  - `PlanRunTopbar` — status pill(含运行中实时秒级 tick)+ 中止 PlanRun 按钮(终态隐藏)+ AlertDialog 二次确认 + 导出报告占位
  - `PlanChainBreadcrumb` — 紧凑单行 Plan 链;current 节点高亮 + 旋转 spinner;blocked 节点(尚未触发的下游)显示"暂不触发"+ tooltip 暴露 `block_reason`;历史节点可点击导航
  - `DispatchGateCard` — 派发门禁卡片;phase 徽章(verifying/syncing/reverifying/ready/failed)+ 主机 × 脚本矩阵(sha256 短码 + matched 标记)+ sync_attempts 计数;终态保留显示用于审计,运行中 ready 自动收起
  - `BusinessFlowTimeline` — 双栏:左侧纵向阶段 stepper(init/patrol/teardown,展开后显示步骤聚合 device_succeeded/failed),右侧事件流(stage + severity 双过滤器 + facet 计数 + 空态)
- **API 客户端扩展**(`frontend/src/utils/api/planRuns.ts`):新增 `getChain` / `getTimeline` / `getEvents` / `getDevices` / `getWatcherSummary` / `abort` / `manualRetryJob` / `manualExitJob` 8 个方法
- **类型扩展**(`frontend/src/utils/api/types.ts`):`PrecheckState` / `PrecheckHostState` / `PrecheckScriptCheck` (ADR-0021 dispatch gate)、`PlanChain` / `PlanRunTimeline` / `PlanRunEventsPayload` / `PlanRunDevicesPayload` / `WatcherSummary` (5 端点),`PlanRun.run_context.precheck` 字段
- **设备总览 + Watcher 卡片**仍是 placeholder,留给 C5c
- **测试**:Vitest 12 cases / 3 文件全过 — `PlanChainBreadcrumb.test.tsx`(3) + `BusinessFlowTimeline.test.tsx`(5) + `PlanRunDetailPage.test.tsx`(4 集成)
- **回归**:全量 frontend `npx vitest run` — 16 文件 / 91 case 通过(`toast.test.tsx` 抛错为其自身 negative case 故意触发,非回归)
- 依赖:无新增

### 2026-05-08 — ADR-0021/0022 C5a₂ — PlanRun 聚合端点 + 复合索引 + Prometheus 监控
- **5 个聚合端点**(`backend/api/routes/plan_runs.py`):
  - `GET /api/v1/plan-runs/{id}/chain` — 沿 `parent_plan_run_id` 回溯 + 候选 next Plan(含 `block_reason`)
  - `GET /api/v1/plan-runs/{id}/timeline` — 三阶段聚合,patrol 含 `patrol_cycle_index` / `active_devices` / `interval_seconds`
  - `GET /api/v1/plan-runs/{id}/events?stage=&severity=&limit=&offset=` — 多源事件流(trigger / step 失败 / log_signal / audit)+ facets + 分页
  - `GET /api/v1/plan-runs/{id}/devices?status=&host_id=` — per-device matrix,`ui_status` 派生(completed/running/failed/risk/backoff/pending)
  - `GET /api/v1/plan-runs/{id}/watcher-summary?window_minutes=` — log_signal 按 category 聚合 + trend(对比上一窗口)+ exceeded
- **新增复合索引**(alembic `e8f9a0b1c2d3`,同步加在 `JobInstance.__table_args__`):
  - `idx_step_trace_job_stage` ON (job_id, stage) — timeline 端点 GROUP BY
  - `idx_step_trace_job_status_ts` ON (job_id, status, original_ts) — events 端点失败 step 时序排序
- **Prometheus 指标族**(`backend/core/metrics.py`,8 个新指标族):
  - `stability_plan_run_terminal_total` / `stability_plan_run_pass_rate` — terminal 聚合时埋点(`plan_run_aggregation.py`)
  - `stability_dispatch_gate_runs_total` / `stability_dispatch_gate_duration_seconds` — `_drive_dispatch_gate` finally 埋点(`plan_precheck.py`)
  - `stability_patrol_heartbeat_total` / `stability_patrol_failure_streak_observed` — patrol-heartbeat 端点埋点
  - `stability_patrol_manual_action_total` — manual-retry / manual-exit 端点埋点
  - `stability_log_signal_total` — `/agent/log-signals` 每条入库 signal 埋点
- **测试**:`backend/tests/api/test_plan_run_aggregation_endpoints.py` 新增 15 cases(全 SQLite 兼容,无需 PostgreSQL)
- 验证:backend 全量 265 passed + 102 skipped

### 2026-05-06 — ADR-0020 Plan-based 编排架构落地
- **架构变更**：WorkflowDefinition + TaskTemplate → Plan + PlanStep；WorkflowRun → PlanRun
- **5 阶段 Alembic migration**：`w0x1y2z3a4b5` (DDL 收紧) → `x1y2z3a4b5c6` (DDL 建表，含 `plan_step.enabled` / `plan.patrol_interval_seconds` / `plan.timeout_seconds` / `plan_run.next_plan_triggered Boolean`，**不再有 `plan.lifecycle` 列**) → `y2z3a4b5c6d7` (DML 定义迁移；按 ADR §2 升级为直列字段) → `z3a4b5c6d7e8` (DML 数据迁移；从 `plan_step` 行重建 `plan_snapshot`，回写 `plan_migration_audit.{old_workflow_run_id,new_plan_run_id}`) → `a4b5c6d7e8f9` (DDL：删除旧表 + `task_schedules.plan_id NOT NULL` + 删除 `task_schedules.{params,target_device_id}` + 幂等收敛 `plan.lifecycle`/`plan.timeout_seconds`)
- **删除旧模块**：`orchestration.py`、`templates.py`、`script_executions.py`、`script_sequences.py`、`dispatcher.py`、`script_execution.py`、`workflow.py`、`script_sequence.py`、`workflow.py` (schema)
- **新模块**：`backend/api/routes/plans.py`、`backend/api/routes/plan_runs.py`、`backend/services/plan_dispatcher.py`、`backend/services/plan_dispatcher_sync.py`、`backend/models/plan.py`、`backend/models/plan_run.py`、`backend/models/plan_migration_audit.py`
- **前端**：6 新页面 (PlanList/PlanEdit/PlanExecute/PlanRunList/PlanRunMatrix/ScriptManagement) + PlanLifecycleEditor，删除 17 个旧页面/API模块
- **API 变更**：`/api/v1/workflows` → `/api/v1/plans`，`/api/v1/workflow-runs` → `/api/v1/plan-runs`
- **JobInstance**：`workflow_run_id`/`task_template_id` → `plan_run_id`/`plan_id` (NOT NULL)
- **TaskSchedule**：`workflow_definition_id`/`task_template_id`/`tool_id`/`task_type` → `plan_id`

### 2026-04-20 — ADR-0018 Watcher 子系统主线完成
- **Stage 5A — Watcher 基础设施**：新建 `backend/agent/watcher/`（sources/batcher/emitter/manager/policy/contracts/exceptions），Alembic `k9f0a1b2c3d4`（watcher 生命周期字段）+ `m1g2h3i4j5k6`（设备 active job 部分唯一索引），`JobLogSignal` ORM + `backend/agent/job_session.py` + `POST /api/v1/agent/log-signals` + `claim` PENDING→RUNNING
- **Stage 5B1 — LogPuller**：per-device async adb pull → NFS + sha256/size_bytes/first_lines 富化；`DeviceLogWatcher.attach_puller` + `_on_pull_done` 回调；`LogWatcherManager` 在 `nfs_base_dir` 非空时注入
- **Stage 5B2 — JobArtifact 独立端点 + ArtifactUploader**：Alembic `n2h3i4j5k6l7` 增补 `source_category` / `source_path_on_device` + `UniqueConstraint(job_id, storage_uri)`；`POST /api/v1/agent/jobs/{job_id}/artifacts` whitelist + PG `ON CONFLICT DO NOTHING` 幂等；`backend/agent/artifact_uploader.py` 单例 fire-and-forget；`DeviceLogWatcher._maybe_submit_artifact` 仅 AEE/VENDOR_AEE 且 pull 成功时转发
- **Stage 6 — JobSession E2E**：`test_job_session_e2e.py` 7 cases 仅 mock adb + HTTP；bugfix `LogWatcherManager._prober_factory` 改 lambda 兼容 keyword-only 签名
- **3 个收口契约**：log_signal 是异常事件权威流 / JobArtifact 是独立异步持久化面 / ArtifactUploader 是 fire-and-forget 不回压 watcher
- **灰度路径**：`STP_WATCHER_ENABLED` 默认 `false`（`backend/agent/main.py:69`），未开启时 Agent 完全回退 ADR-0018 Phase 1-6 路径
- **验证**：5B2 新增 20 passed + 7 skipped；watcher 回归 126 passed + 14 skipped
- 主线 commit：`f366b1b`，改动 37 文件 +9083/-88
- 依赖：无新增

### 2026-04-12 — 双轨合并 Wave 7+8 完成：兼容层彻底移除
- **后端新端点**：`orchestration.py` 新增 `GET /api/v1/jobs` 分页端点（支持 `workflow_id` / `status` 筛选），`JobInstanceOut` 新增 `workflow_definition_id` 字段
- **兼容层拆分**：`tasks.py`（787 行）拆分为 `runs.py`（报告/JIRA/步骤/产物）+ `logs.py`（运行时日志/Agent SSH 日志），然后**删除 `tasks.py`**
- **前端全量迁移完成**：所有生产页面切换到 `api.orchestration.*` / `api.execution.*` / `api.logs.*`；`api.tasks` 命名空间整体移除
- **类型统一**：页面全部使用 `JobInstance`/`WorkflowDefinition` 原生类型
- **URL 简化**：产物下载 `/tasks/{id}/runs/{id}/artifacts/{id}/download` → `/runs/{id}/artifacts/{id}/download`
- **旧框架清理**：删除 `useWebSocket.ts` + `useWebSocket.test.ts`；移除 `websocket_*` 死代码指标；清理 `WebSocketMock`
- **文档同步**：ADR-0006/0007/0009/0018 更新；双轨合并文档标记 Wave 7+8 完成
- 依赖：无新增

---

## 模块职责

稳定性测试管理平台是一个**中心化测试管理系统**，提供：

1. **中心调度**：Windows 服务器运行 FastAPI 后端和 React 前端
2. **Agent 执行**：Linux 主机运行 Python Agent，通过 ADB 连接 Android 设备
3. **实时监控**：设备状态（电量、温度、网络延迟）和主机资源监控
4. **任务管理**：测试任务创建、分发、执行、结果收集

---

## 架构模式

### Windows 主机（中心服务器）
- **FastAPI 后端**：端口 8000，提供 REST API + python-socketio 实时推送
- **APScheduler**：进程内定时调度（recycler / session_watchdog / cron / 数据清理）
- **SAQ Worker**：进程内异步任务队列（post-completion / 通知 / 控制指令）
- **React 前端**：端口 5173，Web Dashboard 界面
- **数据库**：PostgreSQL
- **Redis**：SAQ broker（任务队列）

### Linux Agent 主机
- **Python Agent**：拉取任务、上报心跳、执行测试
- **ADB 连接**：连接 Android 测试设备
- **挂载存储**：NFS 挂载中心存储服务器（172.21.15.4）

### 网络配置
- **子网**：172.21.15.*
- **中心存储**：172.21.15.4（12TB）
- **访问方式**：SSH (Xshell/Xftp)

> **WSL 部署注意事项**：
> 1. 必须先 `rsync` 到 WSL 本地文件系统再运行安装脚本（`/mnt/` 下的 drvfs 有 CRLF 和权限问题）
> 2. 安装前需 `sed -i 's/\r$//' install_agent.sh` 修复 Windows 换行符
> 3. `API_URL` 使用 `http://127.0.0.1:8000`（安装脚本自动检测 WSL 并设置）
> 4. 详细步骤参见 `backend/agent/DEPLOY.md`

---

## 入口与启动

### 后端入口
```bash
# Windows 开发环境
cd stability-test-platform
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端入口
```bash
# Windows 开发环境
cd stability-test-platform/frontend
npm install
npm run dev
```

### Agent 入口

Agent 有两种运行模式：开发模式（从项目源码运行）和部署模式（通过 `install_agent.sh` 安装到 `/opt/`）。

**开发模式**（从项目根目录直接运行）：
```bash
cd stability-test-platform
export API_URL="http://<Windows服务器IP>:8000"
python -m backend.agent.main
```

**部署模式**（通过安装脚本部署后，systemd 管理）：
```bash
# 1. 同步 agent 代码到目标主机
rsync -av --delete backend/agent/ target-host:/tmp/agent-install/

# 2. 在目标主机上运行安装脚本
ssh target-host 'cd /tmp/agent-install && sed -i "s/\r$//" install_agent.sh && sudo bash install_agent.sh'

# 3. 启动服务
sudo systemctl start stability-test-agent
# 或: agentctl start
```

**WSL 部署**（同机模拟 Linux Agent）：
```bash
# 同步代码（从 Windows 文件系统到 WSL 本地，避免 CRLF 和 I/O 问题）
rsync -av --delete /mnt/f/stability-test-platform/backend/agent/ /tmp/agent-install/

# 修复 CRLF 换行符后运行安装
cd /tmp/agent-install
sed -i 's/\r$//' install_agent.sh
sudo bash install_agent.sh
# 交互提示：API_URL 直接回车（自动检测 WSL 使用 127.0.0.1）

# 启动并验证
sudo systemctl start stability-test-agent
sudo systemctl status stability-test-agent
tail -f /opt/stability-test-agent/logs/agent_error.log
```

**WSL Agent 热更新**（代码变更后同步，无需重新安装）：
- 方式一：前端「主机管理」页点击对应主机的「热更新」按钮（单台一键）。
- 方式二：`tools/ansible/playbooks/update_agent.yml`（线下批量；详见 `tools/ansible/README.md`）。

> **WSL 注意事项**：
> - `API_URL` 必须使用 `http://127.0.0.1:8000`（安装脚本自动检测 WSL 并设置）
> - 必须先 `rsync` 到 WSL 本地再安装，不能直接在 `/mnt/` 下执行（CRLF + drvfs 权限问题）
> - 安装前需 `sed -i 's/\r$//'` 修复从 Windows 同步过来的 shell 脚本换行符
> - WSL Agent 使用 `ANDROID_ADB_SERVER_PORT=5039`（见 `/opt/stability-test-agent/.env`）连接 Windows 侧 ADB server

---

## 对外接口

### REST API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/` | API 版本信息 |
| GET | `/docs` | Swagger API 文档 |
| POST | `/api/v1/heartbeat` | 接收 Agent 心跳 |
| GET | `/api/v1/hosts` | 列出所有主机 |
| POST | `/api/v1/hosts` | 创建主机 |
| GET | `/api/v1/devices` | 列出所有设备 |
| POST | `/api/v1/devices` | 创建设备 |
| GET | `/api/v1/plans` | 列出全部 Plan |
| POST | `/api/v1/plans` | 创建 Plan |
| GET | `/api/v1/plans/{id}` | 获取 Plan 详情 |
| PUT | `/api/v1/plans/{id}` | 更新 Plan |
| DELETE | `/api/v1/plans/{id}` | 删除 Plan |
| POST | `/api/v1/plans/{id}/run/preview` | 预览 Plan 扇出 |
| POST | `/api/v1/plans/{id}/run` | 触发 PlanRun |
| GET | `/api/v1/plan-runs` | PlanRun 列表 |
| GET | `/api/v1/plan-runs/{id}` | PlanRun 详情 |
| GET | `/api/v1/plan-runs/{id}/jobs` | PlanRun 关联 Job 列表 |
| GET | `/api/v1/plan-runs/{id}/summary` | PlanRun 聚合概览 |
| GET | `/api/v1/plan-runs/{id}/chain` | Plan 链(parent + current + 候选 next) |
| GET | `/api/v1/plan-runs/{id}/timeline` | 业务流时间线(三阶段聚合 + patrol heartbeat) |
| GET | `/api/v1/plan-runs/{id}/events` | 事件流(trigger/step/log_signal/audit 多源融合,支持 stage/severity 过滤+分页) |
| GET | `/api/v1/plan-runs/{id}/devices` | 设备总览矩阵(含 ui_status 派生 + by_status/by_host facet) |
| GET | `/api/v1/plan-runs/{id}/watcher-summary` | Watcher 异常聚合(按 category + trend) |
| POST | `/api/v1/plan-runs/{id}/abort` | 中止 PlanRun(ADR-0021 D7) |
| POST | `/api/v1/plan-runs/{id}/jobs/{job_id}/manual-retry` | patrol 退避中手动立即重试(ADR-0022 D7) |
| POST | `/api/v1/plan-runs/{id}/jobs/{job_id}/manual-exit` | patrol 退避中手动退出(跳 teardown) |
| GET | `/api/v1/runs/{run_id}/report` | 获取 Job 报告 |
| GET | `/api/v1/runs/{run_id}/report/cached` | 获取缓存 Job 报告 |
| GET | `/api/v1/runs/{run_id}/report/export` | 导出 Job 报告（markdown/json） |
| POST | `/api/v1/runs/{run_id}/jira-draft` | 生成 JIRA 草稿 |
| GET | `/api/v1/runs/{run_id}/jira-draft/cached` | 获取缓存 JIRA 草稿 |
| GET | `/api/v1/runs/{run_id}/steps` | 获取 RunStep 列表 |
| GET | `/api/v1/runs/{run_id}/steps/{step_id}` | 获取单个 RunStep |
| GET | `/api/v1/runs/{run_id}/artifacts/{artifact_id}/download` | 下载产物文件 |
| GET | `/api/v1/logs/query` | 查询运行时日志 |
| POST | `/api/v1/agent/logs` | 查询 Agent SSH 日志 |
| GET | `/api/v1/pipeline/templates` | 列出内置 Pipeline 模板 |
| GET | `/api/v1/pipeline/templates/{name}` | 获取指定 Pipeline 模板 |
| GET | `/api/v1/jobs` | 全量 Job 分页列表（支持 plan_id/status 筛选） |

### Agent API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/agent/jobs/pending` | 获取待执行任务 |
| POST | `/api/v1/agent/jobs/{id}/heartbeat` | 更新任务状态 |
| POST | `/api/v1/agent/jobs/{id}/complete` | 完成任务 |
| POST | `/api/v1/agent/jobs/{id}/extend_lock` | 续期设备锁 |
| POST | `/api/v1/agent/jobs/{job_id}/steps/{step_id}/status` | 更新步骤状态（HTTP fallback） |
| POST | `/api/v1/agent/jobs/{job_id}/patrol-heartbeat` | patrol 周期聚合心跳（ADR-0022） |
| POST | `/api/v1/agent/log-signals` | watcher 异常事件批量上送（ADR-0018） |

### SocketIO 端点

| Namespace | 方向 | 说明 |
|-----------|------|------|
| `/agent` | Agent→Backend | Agent 实时日志/状态/心跳推送（socketio.Client 同步版） |
| `/dashboard` | Backend→Frontend | 前端实时更新推送（socket.io-client） |

> Legacy WS 端点（`/ws/agent/{host_id}`, `/ws/logs/{run_id}`）保留为 deprecated stubs。

### Pipeline 定义格式（pipeline_def）

引擎仅接受 `lifecycle` 顶层键；`stages` 与 `phases` 格式会被拒绝（`pipeline_engine.py` L158-179）。

```json
{
  "lifecycle": {
    "init": [
      {
        "step_id": "check_device",
        "action": "script:check_device",
        "version": "v1.0.0",
        "params": {},
        "timeout_seconds": 30,
        "retry": 0
      }
    ],
    "patrol": {
      "interval_seconds": 60,
      "steps": [ /* ...script step... */ ]
    },
    "teardown": [ /* ...script step... */ ],
    "timeout_seconds": 0
  }
}
```

**Action 类型**:
- `script:<name>` — 由 ScriptRegistry 解析的脚本（python/shell/bat），通过 NFS 路径执行。**唯一支持的 action 类型**。
- ~~`builtin:<name>`~~ — 已删除（2026-05-04，随 `backend/agent/actions/`、`/api/v1/builtin-actions` 一并清理）。
- ~~`tool:<id>`~~ — 已删除（同上批次）。
- ~~`shell:<command>`~~ — 已废弃（详见 ADR-0014）。

---

## 关键依赖与配置

### 后端依赖
```
fastapi
uvicorn[standard]
sqlalchemy
pydantic
python-multipart
paramiko
asyncssh
psutil
requests
aiohttp
apscheduler>=4.0.0a5,<5.0
saq>=0.12.0,<1.0
python-socketio[asyncio]>=5.11.0,<6.0
prometheus-client
```

### 前端依赖
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^7.12.0",
    "@tanstack/react-query": "^4.29.0",
    "axios": "^1.4.0",
    "lucide-react": "^0.562.0",
    "tailwindcss": "^3.3.0",
    "socket.io-client": "^4.8.3"
  }
}
```

### 环境变量

| 变量 | 当前值 / 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+psycopg://stability:stability@localhost:5432/stability` | 数据库连接（Windows 侧 PostgreSQL） |
| `API_URL` | `http://127.0.0.1:8000` | 后端 API 地址 |
| `HOST_ID` | `auto` | 主机 ID（Agent 使用，`auto` 为自动注册） |
| `ADB_PATH` | `adb` | ADB 可执行文件路径 |
| `POLL_INTERVAL` | `10` | Agent 轮询间隔（秒） |
| `ANDROID_ADB_SERVER_PORT` | `5039`（WSL Agent） | WSL 环境必须指定此端口以连接 Windows 侧 ADB server |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接（SAQ broker） |
| `SAQ_CONCURRENCY` | `10` | SAQ Worker 并发数 |
| `AGENT_SECRET` | （空） | Agent SocketIO 连接密钥（生产环境必须设置） |
| `STP_NFS_ROOT` | `/mnt/storage/test-platform` | NFS 挂载根（生产侧），脚本/日志/产物的根目录 |
| `STP_SCRIPT_ROOT` | `${STP_NFS_ROOT}/scripts` | 脚本扫描根。**开发环境必须显式覆盖**为 `<repo>/backend/agent/scripts` |
| `STP_SCRIPT_RUNTIME_ROOT` | （空） | Agent 实际访问脚本的 NFS 路径前缀；扫描机=运行机时留空，跨机时指向 Agent 上的 NFS 挂载点 |
| `STP_WATCHER_ENABLED` | `false` | Agent 侧 Watcher 子系统灰度开关（ADR-0018） |

---

## 数据模型

> **双轨合并完成**：遗留 ORM（schemas.py / legacy.py）和遗留表已全部清除。
> 所有业务逻辑使用 `backend/models/` 下的独立模块（host, job, tool, workflow 等）。
> 详见 `docs/dual-track-merger-v3.revised.md`。

### Host（主机） — `backend/models/host.py`
```python
class Host(Base):
    __tablename__ = "host"
    id: str             # 字符串主键 (如 "host-101")
    hostname: str
    name: Optional[str]
    ip: Optional[str]
    ip_address: Optional[str]
    ssh_port: int
    ssh_user: Optional[str]
    status: str         # ONLINE, OFFLINE, DEGRADED
    last_heartbeat: datetime
    extra: JSON         # cpu_load, ram_usage, disk_usage
    mount_status: JSON
```

### Device（设备） — `backend/models/host.py`
```python
class Device(Base):
    __tablename__ = "device"
    id: int
    serial: str         # 唯一
    model: Optional[str]
    host_id: str        # FK -> host.id (字符串)
    status: str         # ONLINE, OFFLINE, BUSY
    last_seen: datetime
    battery_level: int
    temperature: int
    network_latency: float
    lease_generation: int
```

### Plan（编排计划） — `backend/models/plan.py`
```python
class Plan(Base):
    __tablename__ = "plan"
    id: int
    name: str
    description: Optional[str]
    failure_threshold: float
    patrol_interval_seconds: Optional[int]   # ADR-0020 §2 直列字段
    timeout_seconds: Optional[int]           # ADR-0020 §2 直列字段
    # 注意：不再保留 lifecycle JSONB 列；lifecycle 由 PlanStep 行 + 上述两个直列字段
    # 在 dispatcher 阶段重组成 pipeline_def.lifecycle（唯一事实源）。
    next_plan_id: Optional[int]              # FK -> plan.id (自引用 Plan 链)
    watcher_policy: Optional[JSONB]
    created_by: Optional[str]
    # relationships: steps, runs, next_plan
```

### PlanStep（编排步骤） — `backend/models/plan.py`
```python
class PlanStep(Base):
    __tablename__ = "plan_step"
    id: int
    plan_id: int          # FK -> plan.id
    step_key: str
    script_name: str
    script_version: str
    stage: str            # init, patrol, teardown（命名注记：ADR 早期文档称 phase，列名稳定为 stage）
    sort_order: int
    timeout_seconds: Optional[int]
    retry: int
    enabled: bool         # default true；dispatcher 仅消费 enabled 行
```

### PlanRun（编排执行） — `backend/models/plan_run.py`
```python
class PlanRun(Base):
    __tablename__ = "plan_run"
    id: int
    plan_id: int
    status: str                  # RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, DEGRADED
    failure_threshold: float
    plan_snapshot: JSONB
    run_type: str                # MANUAL, SCHEDULE, CHAIN
    parent_plan_run_id: Optional[int]  # FK -> plan_run.id (Plan 链)
    root_plan_run_id: Optional[int]
    chain_index: int
    triggered_by: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    result_summary: Optional[JSONB]
    # relationships: plan, jobs, parent_run, root_run
```

### JobInstance（任务执行记录） — `backend/models/job.py`
```python
class JobInstance(Base):
    __tablename__ = "job_instance"
    id: int
    plan_run_id: int       # FK -> plan_run.id (NOT NULL)
    plan_id: int           # FK -> plan.id (NOT NULL)
    device_id: int         # FK -> device.id
    host_id: str           # FK -> host.id
    status: str            # PENDING, RUNNING, COMPLETED, FAILED, ABORTED
    status_reason: Optional[str]
    pipeline_def: JSONB
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    report_json: Optional[JSONB]
    jira_draft_json: Optional[JSONB]
    post_processed_at: Optional[datetime]
    # relationships: plan_run, plan, device, host, step_traces, artifacts
```

### StepTrace（步骤执行追踪） — `backend/models/job.py`
```python
class StepTrace(Base):
    __tablename__ = "step_trace"
    id: int
    job_id: int          # FK -> job_instance.id
    step_id: str
    stage: str
    event_type: str
    status: str
    output: Optional[str]
    error_message: Optional[str]
    original_ts: datetime
```

### 其他模型
- **User** — `backend/models/user.py`（认证用户）
- **AuditLog** — `backend/models/audit.py`（审计日志）
- **NotificationChannel / AlertRule** — `backend/models/notification.py`（通知规则）
- **TaskSchedule** — `backend/models/schedule.py`（定时调度）
- **ActionTemplate** — `backend/models/action_template.py`（Action 模板）
- **JobArtifact** — `backend/models/job.py`（Job 产物）
- **Script** — `backend/models/script.py`（脚本目录元数据，详见下章）

---

## 脚本目录与扫描机制（ADR-0020）

> 唯一支持的 action 类型：`script:<name>`。脚本是 Plan 编排的最小执行单元；同名脚本通过 `version` 区分，**版本即参数**——已存在版本的 `default_params` 不允许修改，参数变更必须新建版本。

### 目录契约

```
<STP_SCRIPT_ROOT>/
  <name>/                       ← 一级=脚本名（默认作为 display_name）
    v<version>/                 ← 二级=必须以 v 开头的语义化版本号目录
      <entry>.{py,sh,bat,cmd}   ← 入口=该目录里第一个非 "_" 开头的可识别脚本文件
      _adb.py                   ← "_" 开头的辅助模块在扫描时被跳过
```

仓库现有脚本（`backend/agent/scripts/`，category 固定 `device`）：
`check_device` / `clean_env` / `connect_wifi` / `ensure_root` / `fill_storage` /
`install_apk` / `monkey_check` / `monkey_launch (v1.0.0 + v2.0.0)` /
`monkey_setup` / `monkey_teardown` / `push_resources`

实现位置：`backend/services/script_catalog.py`、`backend/api/routes/scripts.py`、`backend/agent/registry/script_registry.py`。

### 扫描根：dev vs prod 对照

| 场景 | `STP_SCRIPT_ROOT` | `STP_SCRIPT_RUNTIME_ROOT` | 说明 |
|------|------------------|--------------------------|------|
| 开发本机（Windows + 本机 Agent） | `<repo>/backend/agent/scripts` | （空） | 扫描机=运行机，路径直接复用 |
| WSL 联调 | `<repo>/backend/agent/scripts` | `/opt/stability-test-agent/scripts` | 后端在 Windows 扫描，Agent 在 WSL 跑，需要重写 `nfs_path` |
| 生产 | `${STP_NFS_ROOT}/scripts` | 同 `STP_SCRIPT_ROOT`（一般留空） | 后端与 Agent 同时挂载 NFS，路径一致 |

### 扫描行为（POST `/api/v1/scripts/scan`）

| 结果计数 | 含义 | 后续动作 |
|---------|------|---------|
| `created` | 磁盘有、DB 无 → INSERT | 自动 `is_active=true`，`category=device`，`default_params={}` |
| `skipped` | 磁盘 sha256 与 DB 一致 | 若曾被 deactivate，扫描会自动恢复 active |
| `conflicts` | 同 (name, version) 但 sha256 不一致 | **不动 DB**；需手动 `POST /scripts/{name}/versions` 新建版本 |
| `deactivated` | DB 有、当前根下磁盘无 | 标记 `is_active=false`，不删行（保留审计） |

### 字段权属

| 字段 | 扫描入库默认 | 通用 PUT 修改 | 创建新版本 | Agent 运行时是否消费 |
|------|--------------|---------------|-----------|--------------------|
| `name` | 一级目录名 | 允许 | 路径参数 | ✅ 解析 `script:<name>` |
| `display_name` | =name | 允许 | 继承最新版 | ❌ 仅 UI |
| `category` | `device` 固定 | 允许 | 继承最新版 | ❌ 仅 UI 过滤 |
| `script_type` | 后缀映射（py/sh/bat/cmd） | 允许 | 继承最新版 | ✅ 决定 runner |
| `version` | 二级目录 v 后部分 | 允许 | **必填** | ✅ |
| `nfs_path` | runtime_root + 相对路径 | 允许 | **必填** | ✅ subprocess argv |
| `entry_point` | 写空串 | 允许 | 可选 | ❌ 当前未消费（已死字段） |
| `content_sha256` | 文件实际 sha | 允许 | **必填** | ❌ 仅审计 |
| `param_schema` | `{}` | 允许 | 可选 | ❌ 当前未做校验 |
| `default_params` | `{}` | **422 拒绝** | **必填** | ✅ 注入 step.params → `STP_STEP_PARAMS` |
| `is_active` | `true` | 允许 | 自动 true | ✅ ScriptRegistry 仅同步 active |
| `description` | null | 允许 | 可选 | ❌ 仅 UI |

**ADR-0020 不变量**：已存在版本不允许改 `default_params`（`backend/api/routes/scripts.py:214-218` 422 拦截），必须 `POST /api/v1/scripts/{name}/versions` 新建版本。
**前端入口**（`frontend/src/pages/scripts/ScriptManagementPage.tsx`）：仅暴露 *搜索 / 查看参数 / 新建版本* 三个动作；通用 PUT 接口未通过 UI 暴露。

### 完整链路（文件 → DB → Plan → Agent 执行）

```
[1] 文件系统：backend/agent/scripts/<name>/v<version>/<entry>.py
       │  POST /api/v1/scripts/scan
       ▼
[2] DB.script (name, version, nfs_path, content_sha256, default_params, is_active)
       │  Plan 创建：PlanStep(script_name, script_version, stage, sort_order, ...)
       │  ⚠ PlanStep 不存 params；版本即参数
       │  POST /api/v1/plans/{id}/run
       ▼
[3] plan_dispatcher_sync._build_lifecycle_from_steps:
       step_def.params = deepcopy(Script.default_params)   ← 从 DB 取
       → _inject_wifi_params 仅对 connect_wifi 注入资源池 ssid/password
       → 写入 JobInstance.pipeline_def + PlanRun.plan_snapshot
       │  Agent claim 拉到 pipeline_def
       ▼
[4] ScriptRegistry.resolve(name, version) → ScriptEntry(nfs_path, script_type, sha256)
       │
       ▼
[5] pipeline_engine._run_script_action:
       subprocess.run(
         [python|bash|cmd, nfs_path],
         env={STP_DEVICE_SERIAL, STP_ADB_PATH, STP_LOG_DIR, STP_NFS_ROOT, STP_JOB_ID,
              STP_STEP_PARAMS = json.dumps(step.params)},
         timeout = step.timeout_seconds,
         cwd = nfs_path 所在目录)
       │
       ▼
[6] 脚本通过 _adb.py 的 params() 读 STP_STEP_PARAMS → stdout 输出 JSON
       {"success": bool, "metrics": {...}, "skipped": ?, "skip_reason": ?}
       → StepResult → step_trace 上报 → JobStatus 终态 → PlanRun aggregator
```

### 特殊注入：wifi 资源池

`plan_dispatcher_sync._inject_wifi_params` 仅对 action 含 `connect_wifi` 的步骤注入 `{ssid, password, pool_name, pool_id}`，源自 `ResourcePool` 分配。这是当前唯一打破「params 完全来自 default_params」纯抽象的特例。

---

## 测试与质量

### 单元测试
- 位置：`backend/agent/tests/`
- 运行：`pytest backend/`

### 手动测试
1. 启动后端服务
2. 启动前端服务
3. 启动 Agent
4. 访问 http://localhost:5173

---

## 常见问题 (FAQ)

### Q: 如何部署到生产环境？

**Windows 服务器**：
- 使用 Gunicorn + Uvicorn Worker
- 配置 Nginx 反向代理
- 使用 PostgreSQL 数据库

**Linux Agent**：
- 使用 systemd 管理服务
- 配置环境变量文件

### Q: 如何添加新的测试类型？

1. 在 `backend/agent/scripts/<name>/v<version>/` 下放置脚本（python/shell/bat），由 `script_catalog.py` 扫描注册
2. 在 Pipeline 定义中通过 `action: "script:<name>"` 引用
3. 更新前端 PipelineEditor 的步骤模板（如需）

### Q: 脚本如何扫描入库？开发环境路径怎么设？

1. **开发本机**：先设 `STP_SCRIPT_ROOT=<repo>/backend/agent/scripts`（后端启动前的 env 或 `.env`），然后 `POST /api/v1/scripts/scan`
2. **WSL 联调**：再加 `STP_SCRIPT_RUNTIME_ROOT=/opt/stability-test-agent/scripts`，扫描时会重写 `nfs_path`，Agent 才能在 WSL 侧访问到
3. **生产**：`STP_SCRIPT_ROOT` 默认 `${STP_NFS_ROOT}/scripts`（即 `/mnt/storage/test-platform/scripts`），后端与 Agent 同挂 NFS，`runtime_root` 留空
4. 修改已存在版本的 `default_params` 会被 422 拒绝，须 `POST /api/v1/scripts/{name}/versions` 新建版本
5. 完整链路与字段权属表见 *§脚本目录与扫描机制*

### Q: 设备监控指标如何采集？

- `battery_level`：从 `dumpsys battery` 解析
- `temperature`：从 `dumpsys battery` 解析
- `network_latency`：ping 8.8.8.8 / 223.5.5.5（备用）

### Q: 开发环境常见易错项？

**数据库连接**：
- PostgreSQL 运行在 Windows 侧，`DATABASE_URL` 为 `postgresql+psycopg://stability:stability@localhost:5432/stability`
- 使用 `psycopg`（v3 同步驱动）直连时去掉 `+psycopg` 后缀：`postgresql://stability:stability@localhost:5432/stability`
- 数据库表名为单数形式（`device` 非 `devices`，`host` 非 `hosts`）

**WSL Agent ADB 连接**：
- WSL Agent 必须通过 `ANDROID_ADB_SERVER_PORT=5039` 连接到 Windows 侧的 ADB server
- 此配置在 `/opt/stability-test-agent/.env` 中，已在安装时配置
- 手动验证：`ANDROID_ADB_SERVER_PORT=5039 adb devices`（在 WSL 中执行）
- 若忘记配置，Agent 心跳正常但发现设备数为 0

**设备租约（Device Lease）**：
- Job 执行期间设备有 ACTIVE 租约（`device_leases.status = 'ACTIVE'`），status 为 BUSY
- Job 异常终止后 Reconciler（15s 间隔）自动处理过期租约：UNKNOWN → grace → FAILED + 释放
- 紧急手动释放：`UPDATE device_leases SET status = 'RELEASED', released_at = now() WHERE device_id = <id> AND status = 'ACTIVE'`
- 租约续期由 Agent 的 `LeaseRenewer` 负责

**Agent 代码热更新**：
- 修改 `backend/agent/` 下的代码后，必须同步到 WSL/远端并重启 Agent 才能生效
- 单台一键：前端「主机管理」页点击对应主机的「热更新」按钮（后端 `POST /api/v1/hosts/{host_id}/hot-update`）
- 线下批量：`tools/ansible/playbooks/update_agent.yml`（详见 `tools/ansible/README.md`）
- 详见 `backend/agent/DEPLOY.md` 热更新章节

---

## 相关文件清单

### 后端核心
- `backend/main.py` - 应用入口
- `backend/core/database.py` - 数据库配置（同步 + 异步引擎）
- `backend/models/enums.py` - 所有枚举定义（单一源）
- `backend/models/host.py` - Host / Device ORM
- `backend/models/plan.py` - Plan / PlanStep ORM
- `backend/models/plan_run.py` - PlanRun ORM
- `backend/models/job.py` - JobInstance / StepTrace / JobArtifact / JobLogSignal ORM
- `backend/models/user.py` - User ORM
- `backend/models/notification.py` - NotificationChannel / AlertRule ORM
- `backend/models/schedule.py` - TaskSchedule ORM
- `backend/models/audit.py` - AuditLog ORM
- `backend/models/` - 所有 ORM 模型均按领域拆分（plan, plan_run, host, job 等）
- `backend/api/schemas/` - Pydantic 模型（按领域拆分）

### 后端 API
- `backend/api/routes/plans.py` - Plan CRUD + 触发执行 + 预览
- `backend/api/routes/plan_runs.py` - PlanRun 查询 + Job 列表 + 聚合摘要
- `backend/api/routes/hosts.py` - 主机管理
- `backend/api/routes/devices.py` - 设备管理
- `backend/api/routes/runs.py` - Job 报告/JIRA 草稿/步骤/产物下载
- `backend/api/routes/logs.py` - 运行时日志查询/Agent SSH 日志
- `backend/api/routes/heartbeat.py` - 心跳处理
- `backend/api/routes/metrics.py` - Prometheus 指标端点
- `backend/api/routes/pipeline.py` - Pipeline 模板 API
- `backend/api/routes/scripts.py` - Script 管理 API
- `backend/api/routes/schedules.py` - 定时调度 API
- `backend/api/routes/agent_api.py` - Agent 认领/上报

### 基础设施层
- `backend/services/plan_dispatcher.py` - Plan 异步分发器
- `backend/services/plan_dispatcher_sync.py` - Plan 同步分发器
- `backend/services/aggregator.py` / `aggregator_sync.py` - PlanRun 终态聚合
- `backend/scheduler/app_scheduler.py` - APScheduler 4.x 统一调度器
- `backend/scheduler/recycler.py` - Recycler 纯函数（APScheduler job 回调）
- `backend/scheduler/cron_scheduler.py` - Cron 调度纯函数（APScheduler job 回调）
- `backend/tasks/saq_tasks.py` - SAQ 异步任务定义
- `backend/tasks/saq_worker.py` - SAQ Worker 生命周期管理
- `backend/realtime/socketio_server.py` - python-socketio 服务端（/agent + /dashboard）
- `backend/realtime/log_writer.py` - 异步日志文件持久化
- `backend/core/metrics.py` - Prometheus 指标定义与工具函数

### Agent 模块
- `backend/agent/main.py` - Agent 主程序（含 `STP_WATCHER_ENABLED` 灰度开关 L69）
- `backend/agent/config.py` - 集中路径配置
- `backend/agent/heartbeat.py` - 心跳发送
- `backend/agent/device_discovery.py` - 设备发现
- `backend/agent/system_monitor.py` - 系统监控
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎（StepContext.job_id 透传）
- `backend/agent/ws_client.py` - SocketIO 客户端（socketio.Client 同步版）
- `backend/agent/step_trace_uploader.py` - Step 状态 HTTP 批量上报
- `backend/agent/job_session.py` - Job lifecycle 绑定 Watcher start/stop
- `backend/agent/artifact_uploader.py` - ArtifactUploader 单例（fire-and-forget）
- `backend/agent/watcher/` - Watcher 子系统（sources/batcher/emitter/manager/policy/puller/device_watcher）
- `backend/agent/registry/local_db.py` - Agent SQLite（含 `log_signal_outbox` / `watcher_state`）
- `backend/agent/registry/script_registry.py` - ScriptRegistry（解析 `script:<name>` action）

### 前端核心
- `frontend/src/main.tsx` - 应用入口
- `frontend/src/App.tsx` - 根组件
- `frontend/src/router/index.tsx` - 路由配置

### 前端组件
- `frontend/src/pages/Dashboard.tsx` - 仪表盘
- `frontend/src/pages/tasks/TaskDetails.tsx` - 任务详情（Pipeline 步骤树 + xterm.js）
- `frontend/src/components/device/DeviceCard.tsx` - 设备卡片
- `frontend/src/components/network/ConnectivityBadge.tsx` - 连接状态
- `frontend/src/components/pipeline/PipelineEditor.tsx` - Pipeline 可视化编辑器
- `frontend/src/components/pipeline/PipelineStepTree.tsx` - Pipeline 步骤树（运行时视图）
- `frontend/src/components/pipeline/pipelineTypes.ts` - Pipeline 类型定义
- `frontend/src/components/log/XTerminal.tsx` - xterm.js 终端日志组件
- `frontend/src/components/network/HostCard.tsx` - 主机卡片

### 连通性模块
- `backend/connectivity/ssh_verifier.py` - SSH 验证（同步）
- `backend/connectivity/async_ssh_verifier.py` - SSH 验证（异步）
- `backend/connectivity/network_discovery.py` - 网络发现
- `backend/connectivity/mount_checker.py` - 挂载点检查

---

## 下一步建议

1. **告警规则落地**：ADR-0011 第二层——定义 SLO 阈值、配置 Prometheus AlertManager 规则
2. **日志管理**：日志收集、上传、归档（当前由 `log_writer.py` 写入文件系统，后续接入 Loki）
3. **代码同步**：Windows 到 Linux 自动同步脚本
4. **测试工具集成**：封装现有测试工具
5. **水平扩展**：python-socketio Redis adapter 支持多进程消息同步

---

*最后更新时间：2026-05-06 (ADR-0020 脚本目录与扫描机制文档收口)*
