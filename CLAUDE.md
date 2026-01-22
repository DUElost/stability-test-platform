# stability-test-platform - 稳定性测试管理平台

[根目录](../CLAUDE.md) > **stability-test-platform**

---

## 变更记录 (Changelog)

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
- **数据库**：SQLite（可扩展为 PostgreSQL）

### Linux Agent 主机
- **Python Agent**：拉取任务、上报心跳、执行测试
- **ADB 连接**：连接 Android 测试设备
- **挂载存储**：NFS 挂载中心存储服务器（172.21.15.4）

### 网络配置
- **子网**：172.21.15.*
- **中心存储**：172.21.15.4（12TB）
- **访问方式**：SSH (Xshell/Xftp)

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
```bash
# Linux Agent 主机
export API_URL="http://<Windows服务器IP>:8000"
export HOST_ID=1
export ADB_PATH="/usr/bin/adb"

cd stability-test-platform
python -m backend.agent.main
```

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
| POST | `/api/v1/tasks` | 创建任务 |
| POST | `/api/v1/tasks/{id}/dispatch` | 分发任务 |

### Agent API 端点

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | `/api/v1/agent/runs/pending` | 获取待执行任务 |
| POST | `/api/v1/agent/runs/{id}/heartbeat` | 更新任务状态 |
| POST | `/api/v1/agent/runs/{id}/complete` | 完成任务 |

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

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DATABASE_URL` | `sqlite:///./stability.db` | 数据库连接 |
| `API_URL` | `http://127.0.0.1:8000` | 后端 API 地址 |
| `HOST_ID` | `0` | 主机 ID（Agent 使用） |
| `ADB_PATH` | `adb` | ADB 可执行文件路径 |
| `POLL_INTERVAL` | `5` | Agent 轮询间隔（秒） |

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

### Agent 模块
- `backend/agent/main.py` - Agent 主程序
- `backend/agent/heartbeat.py` - 心跳发送
- `backend/agent/device_discovery.py` - 设备发现
- `backend/agent/system_monitor.py` - 系统监控
- `backend/agent/task_executor.py` - 任务执行

### 前端核心
- `frontend/src/main.tsx` - 应用入口
- `frontend/src/App.tsx` - 根组件
- `frontend/src/router/index.tsx` - 路由配置

### 前端组件
- `frontend/src/pages/Dashboard.tsx` - 仪表盘
- `frontend/src/components/device/DeviceCard.tsx` - 设备卡片
- `frontend/src/components/network/ConnectivityBadge.tsx` - 连接状态
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

*最后更新时间：2026-01-22 20:45:40*
