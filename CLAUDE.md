# stability-test-platform - 稳定性测试管理平台

[根目录](../CLAUDE.md) > **stability-test-platform**

---

## 变更记录 (Changelog)

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
```bash
# 方式一：使用 sync_agent.sh（推荐，进入 WSL 执行）
wsl -u root -- bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh wsl

# 方式二：从 Windows 命令行直接同步
wsl -u root -- bash -c "rsync -av --delete --exclude='__pycache__' --exclude='.env' --exclude='*.pyc' /mnt/f/stability-test-platform/backend/agent/ /opt/stability-test-agent/agent/ && systemctl restart stability-test-agent"
```

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
| GET | `/api/v1/jobs` | 全量 Job 分页列表（支持 workflow_id/status 筛选） |
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
| GET | `/api/v1/workflow-runs/{run_id}/jobs/{job_id}/report` | 编排层 Job 报告 |
| POST | `/api/v1/workflow-runs/{run_id}/jobs/{job_id}/jira-draft` | 编排层 JIRA 草稿 |
| GET | `/api/v1/workflow-runs/{run_id}/summary` | Workflow 聚合概览 |

### Agent API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/agent/jobs/pending` | 获取待执行任务 |
| POST | `/api/v1/agent/jobs/{id}/heartbeat` | 更新任务状态 |
| POST | `/api/v1/agent/jobs/{id}/complete` | 完成任务 |
| POST | `/api/v1/agent/jobs/{id}/extend_lock` | 续期设备锁 |
| POST | `/api/v1/agent/jobs/{run_id}/steps/{step_id}/status` | 更新步骤状态（HTTP fallback） |

### SocketIO 端点

| Namespace | 方向 | 说明 |
|-----------|------|------|
| `/agent` | Agent→Backend | Agent 实时日志/状态/心跳推送（socketio.Client 同步版） |
| `/dashboard` | Backend→Frontend | 前端实时更新推送（socket.io-client） |

> Legacy WS 端点（`/ws/agent/{host_id}`, `/ws/logs/{run_id}`）保留为 deprecated stubs。

### Pipeline 定义格式（pipeline_def）

```json
{
  "version": 1,
  "phases": [
    {
      "name": "prepare",
      "parallel": false,
      "steps": [
        {
          "name": "check_device",
          "action": "builtin:check_device",
          "params": {},
          "timeout": 30,
          "on_failure": "stop",
          "max_retries": 0
        }
      ]
    }
  ]
}
```

**Action 类型**:
- `builtin:<name>` — 内置 action（如 `check_device`, `start_process`）
- `tool:<id>` — 注册工具 ID（仅 stages 格式，需 ToolRegistry）
- ~~`shell:<command>`~~ — 已废弃，仅 legacy phases 格式残留，stages/lifecycle 格式不支持（详见 ADR-0014）

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
    lock_run_id: Optional[int]
    lock_expires_at: Optional[datetime]
```

### WorkflowDefinition（工作流定义） — `backend/models/workflow.py`
```python
class WorkflowDefinition(Base):
    __tablename__ = "workflow_definition"
    id: int
    name: str
    description: Optional[str]
    failure_threshold: float
    created_by: Optional[str]
    # relationships: task_templates, runs
```

### TaskTemplate（任务模板） — `backend/models/job.py`
```python
class TaskTemplate(Base):
    __tablename__ = "task_template"
    id: int
    workflow_definition_id: int  # FK -> workflow_definition.id
    name: str
    pipeline_def: JSONB          # Pipeline 定义
    platform_filter: Optional[JSONB]
    sort_order: int
```

### WorkflowRun（工作流执行） — `backend/models/workflow.py`
```python
class WorkflowRun(Base):
    __tablename__ = "workflow_run"
    id: int
    workflow_definition_id: int
    status: str          # RUNNING, SUCCESS, PARTIAL_SUCCESS, FAILED, DEGRADED
    failure_threshold: float
    triggered_by: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    result_summary: Optional[JSONB]
    # relationships: definition, jobs
```

### JobInstance（任务执行记录） — `backend/models/job.py`
```python
class JobInstance(Base):
    __tablename__ = "job_instance"
    id: int
    workflow_run_id: int    # FK -> workflow_run.id
    task_template_id: int   # FK -> task_template.id
    device_id: int          # FK -> device.id
    host_id: str            # FK -> host.id
    status: str             # PENDING, RUNNING, COMPLETED, FAILED, ABORTED
    status_reason: Optional[str]
    pipeline_def: JSONB
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    report_json: Optional[JSONB]
    jira_draft_json: Optional[JSONB]
    post_processed_at: Optional[datetime]
    # relationships: workflow_run, task_template, device, host, step_traces, artifacts
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

### Tool（工具） — `backend/models/tool.py`
```python
class Tool(Base):
    __tablename__ = "tool"
    id: int
    name: str
    version: str
    script_path: str
    script_class: str
    param_schema: JSONB
    is_active: bool
    description: Optional[str]
    category: Optional[str]
```

### 其他模型
- **User** — `backend/models/user.py`（认证用户）
- **AuditLog** — `backend/models/audit.py`（审计日志）
- **NotificationChannel / AlertRule** — `backend/models/notification.py`（通知规则）
- **TaskSchedule** — `backend/models/schedule.py`（定时调度）
- **ActionTemplate** — `backend/models/action_template.py`（Action 模板）
- **JobArtifact** — `backend/models/job.py`（Job 产物）

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

1. 在 `backend/agent/task_executor.py` 添加执行逻辑
2. 更新前端任务类型选项
3. 配置默认参数模板

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

**设备锁（Device Lock）**：
- Job 执行期间设备被锁定（`device.lock_run_id = job_id, status = BUSY`）
- Job 异常终止可能遗留锁，导致后续 Job 卡在 PENDING
- 清理方法：`UPDATE device SET lock_run_id = NULL, lock_expires_at = NULL, status = 'ONLINE' WHERE id = <device_id>`
- 锁自动续期由 Agent 的 `LockRenewalManager` 负责（每 30s 调用 `extend_lock`）

**Agent 代码热更新**：
- 修改 `backend/agent/` 下的代码后，必须同步到 WSL 并重启 Agent 才能生效
- 快速同步：`wsl -u root -- bash /mnt/f/stability-test-platform/backend/agent/sync_agent.sh wsl`
- 详见 `backend/agent/DEPLOY.md` 热更新章节

---

## 相关文件清单

### 后端核心
- `backend/main.py` - 应用入口
- `backend/core/database.py` - 数据库配置（同步 + 异步引擎）
- `backend/models/enums.py` - 所有枚举定义（单一源）
- `backend/models/host.py` - Host / Device ORM
- `backend/models/workflow.py` - WorkflowDefinition / WorkflowRun ORM
- `backend/models/job.py` - TaskTemplate / JobInstance / StepTrace / JobArtifact ORM
- `backend/models/tool.py` - Tool ORM（新模型）
- `backend/models/user.py` - User ORM
- `backend/models/notification.py` - NotificationChannel / AlertRule ORM
- `backend/models/schedule.py` - TaskSchedule ORM
- `backend/models/audit.py` - AuditLog ORM
- `backend/models/` - 所有 ORM 模型均按领域拆分（host, job, tool, workflow 等）
- `backend/api/schemas.py` - Pydantic 模型

### 后端 API
- `backend/api/routes/orchestration.py` - 工作流管理（CRUD + 执行 + 报告）
- `backend/api/routes/hosts.py` - 主机管理
- `backend/api/routes/devices.py` - 设备管理
- `backend/api/routes/runs.py` - Job 报告/JIRA 草稿/步骤/产物下载
- `backend/api/routes/logs.py` - 运行时日志查询/Agent SSH 日志
- `backend/api/routes/tool_catalog.py` - 工具目录 API（新）
- `backend/api/routes/heartbeat.py` - 心跳处理
- `backend/api/routes/websocket.py` - WebSocket 端点（deprecated stubs）
- `backend/api/routes/metrics.py` - Prometheus 指标端点
- `backend/api/routes/pipeline.py` - Pipeline 模板 API

### 基础设施层（ADR-0018）
- `backend/scheduler/app_scheduler.py` - APScheduler 4.x 统一调度器
- `backend/scheduler/recycler.py` - Recycler 纯函数（APScheduler job 回调）
- `backend/scheduler/cron_scheduler.py` - Cron 调度纯函数（APScheduler job 回调）
- `backend/tasks/saq_tasks.py` - SAQ 异步任务定义
- `backend/tasks/saq_worker.py` - SAQ Worker 生命周期管理
- `backend/tasks/session_watchdog.py` - Session Watchdog 纯函数（APScheduler job 回调）
- `backend/realtime/socketio_server.py` - python-socketio 服务端（/agent + /dashboard）
- `backend/realtime/log_writer.py` - 异步日志文件持久化
- `backend/core/metrics.py` - Prometheus 指标定义与工具函数

### Agent 模块
- `backend/agent/main.py` - Agent 主程序（含 `STP_WATCHER_ENABLED` 灰度开关 L69）
- `backend/agent/config.py` - 集中路径配置
- `backend/agent/heartbeat.py` - 心跳发送
- `backend/agent/device_discovery.py` - 设备发现
- `backend/agent/system_monitor.py` - 系统监控
- `backend/agent/task_executor.py` - 任务执行
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎（StepContext.job_id 透传）
- `backend/agent/ws_client.py` - SocketIO 客户端（socketio.Client 同步版）
- `backend/agent/step_trace_uploader.py` - Step 状态 HTTP 批量上报
- `backend/agent/job_session.py` - Job lifecycle 绑定 Watcher start/stop
- `backend/agent/artifact_uploader.py` - ArtifactUploader 单例（fire-and-forget）
- `backend/agent/watcher/` - Watcher 子系统（sources/batcher/emitter/manager/policy/puller/device_watcher）
- `backend/agent/registry/local_db.py` - Agent SQLite（含 `log_signal_outbox` / `watcher_state`）
- `backend/agent/actions/` - 内置 Step Action 库

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
- `frontend/src/components/pipeline/actionCatalog.ts` - 内置 Action 目录
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

*最后更新时间：2026-04-24 (P0/P1 项目结构治理完成)*
