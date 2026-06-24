# stability-test-platform — 稳定性测试管理平台

> 详细设计见 [`docs/design/`](./docs/design/)；开发者速查见 [`AGENTS.md`](./AGENTS.md)；Agent 部署见 [`backend/agent/DEPLOY.md`](./backend/agent/DEPLOY.md)

---

## 模块职责

稳定性测试管理平台是一个**中心化测试管理系统**，提供：

1. **中心调度**：Windows 服务器运行 FastAPI 后端和 React 前端
2. **Agent 执行**：Linux 主机运行 Python Agent，通过 ADB 连接 Android 设备
3. **实时监控**：设备状态（电量、温度、网络延迟）和主机资源监控
4. **任务管理**：测试任务创建、分发、执行、结果收集

---

## 架构不变量

### Windows 主机（中心服务器）
- **FastAPI 后端**：端口 8000，REST API + python-socketio 实时推送
- **APScheduler**：进程内定时调度（9 个注册 job：recycler / session_watchdog / device_lease_reconciler / cron_check / retention_cleanup / saq_queue_depth_poll / precheck_reaper / revoked_token_cleanup / auto_archive_sweep；详见 `app_scheduler.py` `register_schedules`）
- **SAQ Worker**：进程内异步任务队列（post-completion / 通知 / 控制指令）
- **React 前端**：端口 5173，Web Dashboard
- **数据库**：PostgreSQL
- **Redis**：SAQ broker（任务队列；不存储业务数据）

### Linux Agent 主机
- **Python Agent**：拉取任务、上报心跳、执行测试
- **ADB 连接**：连接 Android 测试设备
- **挂载存储**：NFS 挂载中心存储服务器

### 方案 C 存储与归档（ADR-0025）

完整说明见 [`docs/design/2026-plan-c-storage-and-access.md`](./docs/design/2026-plan-c-storage-and-access.md)。

| 存储 | 用途 | 默认路径 / 访问 |
|------|------|-----------------|
| Agent SSD | 运行日志（唯一物理副本） | `logs/runs/{job_id}/`；HTTP `:8900/run-logs/{job_id}` |
| Agent HDD | AEE + mobilelog + bugreport（第一落点） | `/mnt/hdd/aee_events`（`STP_AEE_LOCAL_ROOT`） |
| 15.4 CIFS | 汇总 xls、按需事件、HDD 溢出 | `STP_AEE_CIFS_ROOT`；**不含**运行日志 |

**已取消（勿在新 Plan 中依赖）**：运行日志 tar/目录树上送 15.4、`run_log_bundle` JobArtifact 注册、patrol cycle 快照 `snapshots/`。

**实施状态**：Sprint 2-4 已合并（PR #31/#34/#35）；Sprint 5 ScanRunner 真机验证待完成。

### ADR-0024 安全约束（生产 guard）

`ENV=production` 启动时强制校验，否则 `RuntimeError`：
- `AUTH_COOKIE_SECURE=1`、`AUTH_COOKIE_SAMESITE ∈ {lax, strict}`
- `STP_CSRF_ENABLED` 必须开启
- HttpOnly Cookie + CSRF Origin 中间件 + refresh token 黑名单

---

## 对外接口

### REST API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET\|POST | `/api/v1/plans` | Plan 列表 / 创建 |
| GET\|PUT\|DELETE | `/api/v1/plans/{id}` | Plan 详情 / 更新 / 删除 |
| POST | `/api/v1/plans/{id}/run` | 触发 PlanRun |
| POST | `/api/v1/plans/{id}/run/preview` | 预览 Plan 扇出 |
| GET | `/api/v1/plan-runs` | PlanRun 列表 |
| GET | `/api/v1/plan-runs/{id}` | PlanRun 详情 |
| GET | `/api/v1/plan-runs/{id}/jobs` | PlanRun 关联 Job 列表 |
| GET | `/api/v1/plan-runs/{id}/summary` | PlanRun 聚合概览 |
| GET | `/api/v1/plan-runs/{id}/chain` | Plan 链（parent + current + 候选 next） |
| GET | `/api/v1/plan-runs/{id}/timeline` | 业务流时间线（三阶段 + patrol heartbeat） |
| GET | `/api/v1/plan-runs/{id}/events` | 事件流（trigger/step/log_signal/audit 多源融合） |
| GET | `/api/v1/plan-runs/{id}/devices` | 设备总览矩阵（含 ui_status 派生） |
| GET | `/api/v1/plan-runs/{id}/watcher-summary` | Watcher 异常聚合（按 category + trend） |
| GET | `/api/v1/plan-runs/{id}/crash-details` | AEE crash 事件详情（ADR-0025） |
| GET | `/api/v1/plan-runs/{id}/report/export` | 导出 PlanRun 报告 |
| POST | `/api/v1/plan-runs/{id}/archive` | 手动触发日志归档（ADR-0025） |
| POST | `/api/v1/plan-runs/{id}/retry-dispatch` | 重新触发派发门禁 |
| POST | `/api/v1/plan-runs/{id}/abort` | 中止 PlanRun |
| POST | `/api/v1/plan-runs/{id}/jobs/{job_id}/manual-retry` | patrol 手动重试（ADR-0022） |
| POST | `/api/v1/plan-runs/{id}/jobs/{job_id}/manual-exit` | patrol 手动退出 |
| GET\|POST | `/api/v1/hosts` | 主机列表 / 创建 |
| GET\|POST | `/api/v1/devices` | 设备列表 / 创建 |
| GET | `/api/v1/runs/{id}/report` | Job 报告 |
| GET | `/api/v1/runs/{id}/report/cached` | 缓存 Job 报告 |
| GET | `/api/v1/runs/{id}/report/export` | 导出 Job 报告 |
| POST | `/api/v1/runs/{id}/jira-draft` | 生成 JIRA 草稿 |
| GET | `/api/v1/runs/{id}/jira-draft/cached` | 缓存 JIRA 草稿 |
| GET | `/api/v1/runs/{id}/steps` | RunStep 列表 |
| GET | `/api/v1/runs/{id}/steps/{step_id}` | 单个 RunStep |
| GET | `/api/v1/runs/{id}/artifacts/{aid}/download` | 下载产物文件 |
| GET | `/api/v1/logs/query` | 运行时日志查询 |
| POST | `/api/v1/agent/logs` | Agent SSH 日志 |
| GET | `/api/v1/pipeline/templates` | Pipeline 模板列表 |
| GET | `/api/v1/pipeline/templates/{name}` | 指定 Pipeline 模板 |
| GET | `/api/v1/jobs` | Job 分页列表（支持 plan_id/status 筛选） |
| GET\|POST | `/api/v1/heartbeat` | Agent 心跳 |
| POST | `/api/v1/auth/login` · `token` · `refresh` · `logout` · `register` | 认证（详见 ADR-0024） |
| GET | `/api/v1/auth/me` | 当前用户 |
| GET | `/api/v1/users` | 用户列表 |
| GET | `/api/v1/audit-logs` | 审计日志 |
| GET | `/api/v1/notifications` | 通知规则 |
| GET | `/api/v1/resource-pools` | WiFi 资源池 |
| GET | `/api/v1/scripts` · `scripts/scan` | 脚本目录查询 / 扫描 |
| GET | `/api/v1/schedules` | 定时调度 |
| GET | `/metrics` | Prometheus 指标 |

### Agent API 端点（X-Agent-Secret 认证）

| 方法 | 端点 | 说明 |
|------|------|------|
| POST | `/api/v1/agent/heartbeat` | 主机级心跳上报 |
| GET | `/api/v1/agent/jobs/pending` | ⚠ **已废弃**（Sunset 2026-11-01），用 `POST /jobs/claim` |
| POST | `/api/v1/agent/jobs/claim` | 认领待执行任务 |
| POST | `/api/v1/agent/jobs/{id}/status` | 更新任务状态 |
| POST | `/api/v1/agent/jobs/{id}/heartbeat` | 任务级心跳 |
| POST | `/api/v1/agent/jobs/{id}/complete` | 完成任务 |
| POST | `/api/v1/agent/jobs/{id}/extend_lock` | 续期设备锁 |
| POST | `/api/v1/agent/steps` | 批量 upsert StepTrace |
| POST | `/api/v1/agent/jobs/{id}/steps/{step_id}/status` | 更新单个步骤状态 |
| POST | `/api/v1/agent/jobs/{id}/patrol-heartbeat` | patrol 周期聚合心跳（ADR-0022） |
| POST | `/api/v1/agent/log-signals` | watcher 异常事件批量上送（ADR-0018） |
| POST | `/api/v1/agent/jobs/{id}/artifacts` | 上报 JobArtifact（ADR-0018） |
| POST | `/api/v1/agent/recovery/sync` | crash recovery 同步（ADR-0019） |
| GET | `/api/v1/agent/{host_id}/archive-status` | ADR-0025 归档状态查询 |

### SocketIO

| Namespace | 方向 | 说明 |
|-----------|------|------|
| `/agent` | Agent→Backend | 实时日志/状态/心跳推送 |
| `/dashboard` | Backend→Frontend | 前端实时更新推送 |

---

## Pipeline 契约

引擎**仅接受** `lifecycle` 顶层键；`stages` / `phases` 格式会被拒绝（`pipeline_engine.py` `PipelineEngine.execute()` 内）。

**唯一 action 类型**：`script:<name>`（由 ScriptRegistry 解析的脚本）。`builtin:<name>` / `tool:<id>` / `shell:<command>` 已全部删除。

```json
{
  "lifecycle": {
    "init": [{ "step_id": "check_device", "action": "script:check_device", "version": "v1.0.0", "params": {}, "timeout_seconds": 30, "retry": 0 }],
    "patrol": { "interval_seconds": 60, "steps": [ /* ... */ ] },
    "teardown": [ /* ... */ ],
    "timeout_seconds": 0
  }
}
```

---

## 脚本目录与扫描机制（ADR-0020）

**版本即参数**：已存在版本的 `default_params` 不允许修改（`scripts.py` `create_script_version` 内 422 拦截），参数变更必须新建版本。

### 目录契约

```
<STP_SCRIPT_ROOT>/
  <name>/                       ← 一级=脚本名（默认作为 display_name）
    v<version>/                 ← 二级=必须以 v 开头的语义化版本号目录
      <entry>.{py,sh,bat,cmd}   ← 入口=该目录里第一个非 "_" 开头的可识别脚本文件
      _adb.py                   ← "_" 开头的辅助模块在扫描时被跳过
```

仓库现有脚本见 `backend/agent/scripts/` 目录；monkey lifecycle 推荐链见 `docs/design/01-execution-pipeline.md`。

实现位置：`backend/services/script_catalog.py`、`backend/api/routes/scripts.py`、`backend/agent/registry/script_registry.py`。

### 扫描根：dev vs prod 对照

| 场景 | `STP_SCRIPT_ROOT` | `STP_SCRIPT_RUNTIME_ROOT` | 说明 |
|------|------------------|--------------------------|------|
| 开发本机 | `<repo>/backend/agent/scripts` | （空） | 扫描机=运行机，路径直接复用 |
| WSL 联调 | `<repo>/backend/agent/scripts` | `/opt/stability-test-agent/scripts` | 后端在 Windows 扫描，Agent 在 WSL 跑，需重写 `nfs_path` |
| 生产 | `${STP_NFS_ROOT}/scripts` | （一般留空） | 后端与 Agent 同挂 NFS |

### 扫描行为（POST `/api/v1/scripts/scan`）

| 结果计数 | 含义 | 后续动作 |
|---------|------|---------|
| `created` | 磁盘有、DB 无 → INSERT | 自动 `is_active=true`，`default_params={}` |
| `skipped` | 磁盘 sha256 与 DB 一致 | 曾 deactivate 者自动恢复 active |
| `conflicts` | 同 (name, version) 但 sha256 不一致 | **不动 DB**；需新建版本 |
| `deactivated` | DB 有、磁盘无 | 标记 `is_active=false`，不删行 |

### 字段权属

| 字段 | 扫描入库默认 | 通用 PUT | 创建新版本 | Agent 消费 |
|------|-------------|---------|-----------|-----------|
| `name` | 一级目录名 | 允许 | 路径参数 | ✅ 解析 `script:<name>` |
| `version` | 二级目录 v 后部分 | 允许 | **必填** | ✅ |
| `nfs_path` | runtime_root + 相对路径 | 允许 | **必填** | ✅ subprocess argv |
| `default_params` | `{}` | **422 拒绝** | **必填** | ✅ 注入 step.params → `STP_STEP_PARAMS` |
| `is_active` | `true` | 允许 | 自动 true | ✅ ScriptRegistry 仅同步 active |
| `content_sha256` | 文件实际 sha | 允许 | **必填** | ❌ 仅审计 |

> 前端入口（`ScriptManagementPage.tsx`）：仅暴露搜索 / 查看参数 / 新建版本；通用 PUT 未通过 UI 暴露。

### 完整链路（文件 → DB → Plan → Agent 执行）

```
[1] 文件系统：backend/agent/scripts/<name>/v<version>/<entry>.py
       │  POST /api/v1/scripts/scan
       ▼
[2] DB.script (name, version, nfs_path, content_sha256, default_params, is_active)
       │  Plan 创建：PlanStep(script_name, script_version, stage, sort_order)
       │  ⚠ PlanStep 不存 params；版本即参数
       │  POST /api/v1/plans/{id}/run
       ▼
[3] plan_dispatcher_sync._build_lifecycle_from_steps:
       step_def.params = deepcopy(Script.default_params) ← 从 DB 取
       → _inject_wifi_params 仅对 connect_wifi 注入资源池 ssid/password
       → 写入 JobInstance.pipeline_def + PlanRun.plan_snapshot
       │  Agent claim 拉到 pipeline_def
       ▼
[4] ScriptRegistry.resolve(name, version) → ScriptEntry(nfs_path, script_type, sha256)
       ▼
[5] pipeline_engine._run_script_action:
       subprocess.run([python|bash|cmd, nfs_path],
         env={STP_DEVICE_SERIAL, STP_ADB_PATH, STP_LOG_DIR, STP_NFS_ROOT, STP_JOB_ID,
              STP_STEP_PARAMS = json.dumps(step.params)},
         timeout=step.timeout_seconds, cwd=nfs_path 所在目录)
       ▼
[6] 脚本 stdout → JSON {"success", "metrics", "skipped", "skip_reason"}
       → StepResult → step_trace → JobStatus 终态 → PlanRun aggregator
```

---

## 数据模型约束表

> 完整字段见 [`docs/design/05-data-model.md`](./docs/design/05-data-model.md)；源码见 `backend/models/`。

| 模型 | 表名 | 关键约束 |
|------|------|----------|
| Host | `host` | 字符串 PK（如 "host-101"）；`extra` JSON 含 cpu/ram/disk；status ∈ {ONLINE, OFFLINE, DEGRADED} |
| Device | `device` | `serial` 唯一；FK→host.id；`lease_generation` 乐观锁；status ∈ {ONLINE, OFFLINE, BUSY} |
| Plan | `plan` | **无 lifecycle 列**；派发时从 PlanStep 行 + 直列字段(`patrol_interval_seconds`, `timeout_seconds`)组装；`next_plan_id` 自引用链 |
| PlanStep | `plan_step` | FK→plan.id；`stage` ∈ {init, patrol, teardown}；`enabled` 过滤 dispatcher 消费行 |
| PlanRun | `plan_run` | status ∈ {RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, DEGRADED}；`plan_snapshot` 派发时写入；自引用 parent/root |
| JobInstance | `job_instance` | `plan_run_id`/`plan_id` **NOT NULL**；status ∈ {PENDING, RUNNING, COMPLETED, FAILED, ABORTED, UNKNOWN}；pipeline_def 完整 lifecycle |
| StepTrace | `step_trace` | FK→job_instance.id |
| JobArtifact | `job_artifact` | `UniqueConstraint(job_id, storage_uri)` |
| JobLogSignal | `job_log_signal` | Watcher 异常事件 |
| Script | `script` | `UniqueConstraint(name, version)`；`default_params` 已存在版本 422 不可变；`is_active` 过滤 ScriptRegistry |
| DeviceLease | `device_leases` | 设备租约；reconciler 多节奏扫描（15s） |
| RevokedRefreshToken | `revoked_refresh_token` | PK=jti；APScheduler 每日清理 expired |
| 其他 | — | User / AuditLog / NotificationChannel / AlertRule / TaskSchedule / ActionTemplate / PlanRunArtifact / PlanMigrationAudit / ResourcePool — 详见 `docs/design/05-data-model.md` |

**状态机**：
- **Job**：`PENDING → RUNNING → COMPLETED / FAILED / ABORTED / UNKNOWN`；`PENDING → FAILED`（recycler 超时）；`UNKNOWN → RUNNING / COMPLETED / FAILED`
- **PlanRun**：`RUNNING → SUCCESS / PARTIAL_SUCCESS / FAILED / DEGRADED`

---

## 开发环境陷阱

**数据库连接**：
- 默认 `postgresql+asyncpg://stability:stability@localhost:5432/stability`（异步驱动）
- psycopg3 同步直连改为 `postgresql://...`（去掉驱动后缀）
- 表名单数形式（`device` 非 `devices`）

**WSL Agent ADB 连接**：
- `ANDROID_ADB_SERVER_PORT=5039`（见 `/opt/stability-test-agent/.env`）
- 忘记配置则心跳正常但发现设备数为 0

**WSL 安装**：
- 必须 `rsync` 到 WSL 本地文件系统再运行（`/mnt/` 下 drvfs 有 CRLF + 权限问题）
- 安装前 `sed -i 's/\r$//'` 修复换行符
- 详见 `backend/agent/DEPLOY.md`

**设备租约**：
- Job 异常终止后 Reconciler（15s）自动处理：UNKNOWN → grace → FAILED + 释放
- 紧急手动释放：`UPDATE device_leases SET status='RELEASED', released_at=now() WHERE device_id=<id> AND status='ACTIVE'`

**Agent 热更新**：
- 单台：前端「主机管理」→「热更新」按钮
- 批量：`tools/ansible/playbooks/update_agent.yml`

**开发启动**（三条命令，其余见 `AGENTS.md` §Dev commands）：
```bash
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000   # 后端
cd frontend && npm install && npm run dev                        # 前端
API_URL="http://<IP>:8000" python -m backend.agent.main          # Agent（开发模式）
```

---

## 决策记录

| 日期 | ADR | 决策 |
|------|-----|------|
| 2026-06-21 | 0025 | 方案 C 存储归档：运行日志留 Agent SSD、AEE 落 HDD、15.4 CIFS 仅汇总 xls；取消 run_log_bundle |
| 2026-06-21 | DOC | 文档体系补强：design/ 00-06 + Plan C PRD + 归档清理 |
| 2026-05-21 | 0024 | HttpOnly Cookie + CSRF Origin + refresh 黑名单 + 生产 guard 启动校验 |
| 2026-05-08 | 0021/22 | PlanRunDetailPage(C5b/c) + 设备总览 + Watcher 聚合 + 5 聚合端点 + 主机热更新二次确认(C6) |
| 2026-05-06 | 0020 | Workflow→Plan 架构迁移；5 阶段 alembic；lifecycle 由 PlanStep 行 + 直列字段重组 |
| 2026-05 | 0023 | 脚本溯源与 sha256 契约 |
| 2026-04-28 | 0019 | Device Lease + capacity 调度 + fencing_token |
| 2026-04-20 | 0018 | Watcher 子系统主线：sources/batcher/emitter/manager/policy/contracts |
| 2026-04-12 | — | 双轨合并 Wave 7+8：兼容层彻底移除 |

详细实施见对应 ADR 原文（`docs/adr/`）；测试命令见 `AGENTS.md` §Dev commands / §Test quirks。

---

## 关键环境变量

> 仅列关键项，完整清单见各 `.env.example`（后端 `backend/.env.example`、Agent `backend/agent/.env.example`、前端 `frontend/.env.example`）。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `postgresql+asyncpg://stability:stability@localhost:5432/stability` | 异步驱动；同步连接改用 `postgresql://`（去掉驱动后缀） |
| `API_URL` | `http://127.0.0.1:8000` | 后端 API 地址 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | SAQ broker |
| `AGENT_SECRET` | （空） | 生产必须设置 |
| `STP_NFS_ROOT` | `/mnt/storage/test-platform` | NFS 挂载根（生产侧） |
| `STP_SCRIPT_ROOT` | `${STP_NFS_ROOT}/scripts` | **开发环境必须覆盖**为 `<repo>/backend/agent/scripts` |
| `STP_SCRIPT_RUNTIME_ROOT` | （空） | 跨机时指向 Agent NFS 挂载点 |
| `STP_WATCHER_ENABLED` | `true` | Agent Watcher 开关（设 `"false"` 关闭） |
| `ANDROID_ADB_SERVER_PORT` | `5039`（WSL） | WSL 必须指定以连接 Windows 侧 ADB |
| `AUTH_COOKIE_SECURE` / `AUTH_COOKIE_SAMESITE` / `STP_CSRF_ENABLED` | — | 生产必须满足约束（见 ADR-0024），否则 RuntimeError |
| `STP_AEE_LOCAL_ROOT` | `/mnt/hdd/aee_events` | Agent HDD AEE 事件存储（ADR-0025） |
| `STP_AEE_CIFS_ROOT` | （空，回退 `STP_AEE_NFS_ROOT`） | CIFS 归档目标（ADR-0025） |

---

## 文件索引

| 领域 | 路径 | 说明 |
|------|------|------|
| 后端入口 | `backend/main.py` | 应用入口 + lifespan |
| 后端 API | `backend/api/routes/*.py`（21 模块） | REST 路由；映射见 `docs/design/02-backend.md` |
| 后端模型 | `backend/models/*.py` | ORM（含 `enums.py` 枚举单一源）；详见 `docs/design/05-data-model.md` |
| 后端服务 | `backend/services/*.py` | 派发 / 聚合 / 门禁 / 租约 / 去重 / 状态机 / 后处理 |
| 后端调度 | `backend/scheduler/*.py` | APScheduler 9 job 回调 |
| 后端异步 | `backend/tasks/*.py` | SAQ worker + task 定义 |
| 后端实时 | `backend/realtime/*.py` | SocketIO server + log writer |
| 后端核心 | `backend/core/*.py` | DB / 安全 / CSRF / 指标 / 限流 |
| Agent | `backend/agent/*.py` + 子目录 | 详见 `docs/design/04-agent.md` 及 `DEPLOY.md`；registy/ 含 SQLite + ScriptRegistry；watcher/ 含 10 个 .py 扁平模块 |
| 前端页面 | `frontend/src/pages/**/*.tsx` | 路由映射见 `docs/design/03-frontend.md` |
| 前端组件 | `frontend/src/components/**/*.tsx` | plan-run/ 域最大；pipeline/ 含 PlanCanvas + PlanStepInspector |
| 前端 API | `frontend/src/utils/api/*.ts` | client + types.ts 类型权威源 |
| 连通性 | `backend/connectivity/*.py` | SSH / 挂载检查 |

---

*最后更新：2026-06-24（精简重组：Changelog→决策摘要表、ORM→约束表、去重、FAQ 融入、依赖列表外移）*
