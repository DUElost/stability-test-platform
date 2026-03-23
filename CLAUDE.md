# stability-test-platform - 稳定性测试管理平台

[根目录](../CLAUDE.md) > **stability-test-platform**

---

## 变更记录 (Changelog)

### 2026-02-23
- Pipeline Editor：新增 `PipelineEditor.tsx` 可视化编辑器（Phase/Step CRUD、拖拽排序、Action 选择器、JSON 预览）
- Pipeline Templates API：新增 `GET /api/v1/pipeline/templates` 端点服务内置模板
- CreateTask 集成：Pipeline 编辑器作为第 3 步嵌入任务创建流程
- 前端类型更新：`Task` 接口和 `api.tasks.create` 新增 `pipeline_def` 字段
- Log Fold Groups：Agent 发出 OSC 633 折叠标记，前端渲染为样式化区域分隔符
- Agent `.env.example`：新增 WebSocket 配置变量（WS_URL, AGENT_SECRET, WS_RECONNECT_MAX_DELAY 等）
- 依赖：新增 `@dnd-kit/core`, `@dnd-kit/sortable`, `@dnd-kit/utilities`

### 2026-01-22
- 设备监控：新增 `network_latency` 指标（ping 8.8.8.8 / 223.5.5.5）
- 后端：`device_discovery.py` 实现备用 DNS 逻辑
- 后端：`heartbeat.py` 持久化 `network_latency`，移除 WiFi 字段
- 前端：`DeviceCard.tsx` 使用 `ConnectivityBadge` 替代 WiFi dBm 显示

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
- **FastAPI 后端**：端口 8000，提供 REST API 和 WebSocket
- **React 前端**：端口 5173，Web Dashboard 界面
- **数据库**：PostgreSQL

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
| GET | `/api/v1/tasks` | 列出所有任务 |
| POST | `/api/v1/tasks` | 创建任务（支持 `pipeline_def` 字段） |
| POST | `/api/v1/tasks/{id}/dispatch` | 分发任务 |
| GET | `/api/v1/runs/{run_id}/steps` | 获取 RunStep 列表 |
| GET | `/api/v1/runs/{run_id}/steps/{step_id}` | 获取单个 RunStep |
| GET | `/api/v1/pipeline/templates` | 列出内置 Pipeline 模板 |
| GET | `/api/v1/pipeline/templates/{name}` | 获取指定 Pipeline 模板 |

### Agent API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/agent/runs/pending` | 获取待执行任务 |
| POST | `/api/v1/agent/runs/{id}/heartbeat` | 更新任务状态 |
| POST | `/api/v1/agent/runs/{id}/complete` | 完成任务 |
| POST | `/api/v1/agent/runs/{run_id}/steps/{step_id}/status` | 更新步骤状态（HTTP fallback） |

### WebSocket 端点

| 端点 | 方向 | 说明 |
|------|------|------|
| `WS /ws/agent/{host_id}` | Agent→Backend | Agent 实时日志/状态推送 |
| `WS /ws/logs/{run_id}` | Backend→Frontend | 前端日志订阅 |

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
    "tailwindcss": "^3.3.0"
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
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Redis 连接（实时日志流） |

---

## 数据模型

### Host（主机）
```python
class Host(Base):
    id: int
    name: str
    ip: str
    ssh_port: int
    ssh_user: Optional[str]
    status: HostStatus  # ONLINE, OFFLINE, DEGRADED
    last_heartbeat: datetime
    extra: JSON  # cpu_load, ram_usage, disk_usage
    mount_status: JSON
```

### Device（设备）
```python
class Device(Base):
    id: int
    serial: str
    model: Optional[str]
    host_id: int
    status: DeviceStatus  # ONLINE, OFFLINE, BUSY
    last_seen: datetime
    extra: JSON  # battery_level, temperature, network_latency
```

### Task（任务）
```python
class Task(Base):
    id: int
    name: str
    type: str  # MONKEY, MTBF, DDR, GPU, STANDBY
    params: JSON
    target_device_id: int
    status: TaskStatus
    priority: int
```

### TaskRun（任务执行记录）
```python
class TaskRun(Base):
    id: int
    task_id: int
    host_id: int
    device_id: int
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    exit_code: int
    error_message: str
    log_summary: str
```

---

## 测试与质量

### 单元测试
- 位置：`backend/agent/test_agent.py`
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
- `backend/core/database.py` - 数据库配置
- `backend/models/schemas.py` - ORM 模型
- `backend/api/schemas.py` - Pydantic 模型

### 后端 API
- `backend/api/routes/hosts.py` - 主机管理
- `backend/api/routes/devices.py` - 设备管理
- `backend/api/routes/tasks.py` - 任务管理
- `backend/api/routes/heartbeat.py` - 心跳处理
- `backend/api/routes/websocket.py` - WebSocket 端点（Agent + Frontend）
- `backend/api/routes/pipeline.py` - Pipeline 模板 API

### Agent 模块
- `backend/agent/main.py` - Agent 主程序
- `backend/agent/config.py` - 集中路径配置
- `backend/agent/heartbeat.py` - 心跳发送
- `backend/agent/device_discovery.py` - 设备发现
- `backend/agent/system_monitor.py` - 系统监控
- `backend/agent/task_executor.py` - 任务执行
- `backend/agent/pipeline_engine.py` - Pipeline 执行引擎
- `backend/agent/ws_client.py` - WebSocket 客户端
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

1. **任务调度器**：实现完整的任务调度逻辑
2. **WebSocket 推送**：实时状态更新
3. **日志管理**：日志收集、上传、归档
4. **代码同步**：Windows 到 Linux 自动同步脚本
5. **测试工具集成**：封装现有测试工具

---

*最后更新时间：2026-03-23 17:50:00*
