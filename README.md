# Stability Test Platform - 启动指南

**版本**：1.0.0
**更新时间**：2026-01-21

---

## 项目目标愿景（请先阅读）

- 项目目标：通过 Linux Host Agent 集群，无人值守运行大规模 Android Device 稳定性自动化测试。
- 稳定性专项统一流程：设备连接检测 -> 前置准备 -> 资源填充 -> 开始运行测试 -> 日志检测 -> 风险问题检查 -> 日志回传导出 -> 结束测试 -> 测试后置。
- 全流程自动化衔接：结果收取 -> 数据报告生成 -> JIRA 问题提交 -> 测试报告生成。
- 详细说明：`docs/project-vision.md`

---

## 架构决策记录（ADR）快速入口

- ADR 总入口与维护规范：`docs/adr/README.md`
- 已落地架构基线（先读）：`docs/adr/ADR-0001-control-plane-and-agent-architecture.md` ~ `docs/adr/ADR-0007-tool-template-workflow-extension-model.md`
- 未来扩展/重构路线：`docs/adr/ADR-0008-schema-migration-governance-alembic-only.md` ~ `docs/adr/ADR-0012-post-completion-pipeline-jira-automation.md`
- 推荐阅读顺序：先 `Accepted`，再 `Proposed`；变更代码前先检索对应 ADR。

---

## 快速启动

### 方式一：Windows 批处理脚本（推荐）

1. **启动后端服务**
   ```bash
   cd stability-test-platform
   start-backend.bat
   ```
   服务将运行在 `http://localhost:8000`

2. **启动前端服务**（新开终端）
   ```bash
   cd stability-test-platform
   start-frontend.bat
   ```
   服务将运行在 `http://localhost:5173`

3. **访问平台**
   - 打开浏览器访问：`http://localhost:5173`

### 方式二：手动启动

#### 启动后端

```bash
# 进入项目目录
cd stability-test-platform

# (可选) 创建并激活虚拟环境
python -m venv venv
# Windows:
venv\Scripts\activate
# Linux/Mac:
# source venv/bin/activate

# 安装依赖
pip install fastapi uvicorn sqlalchemy pydantic paramiko asyncssh psutil requests aiohttp

# 启动服务
uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000
```

#### 启动前端

```bash
# 进入前端目录
cd stability-test-platform/frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

---

## 平台架构

```
┌─────────────────────────────────────────┐
│          FastAPI 后端服务               │
│  (端口 8000 - API + WebSocket)          │
└──────────────────┬──────────────────────┘
                   │
    ┌──────────────┼──────────────┐
    │              │              │
┌───▼────┐   ┌────▼───┐   ┌─────▼────┐
│ React  │   │ Host   │   │ Host     │
│ 前端    │   │ Agent 1│   │ Agent N  │
│ (5173)  │   │ (Linux)│   │ (Linux)  │
└────────┘   └────────┘   └──────────┘
```

---

## 环境要求

### 后端
- **Python**：3.10+（推荐 3.11）
- **依赖包**：
  - fastapi
  - uvicorn
  - sqlalchemy
  - pydantic
  - paramiko
  - asyncssh
  - psutil
  - requests
  - aiohttp

### 前端
- **Node.js**：20+
- **npm**：10.0+

### 可选（Linux Agent 主机）
- **ADB**：Android Debug Bridge
- **Python 3.10+**
- **SSH 访问权限**

---

## 配置说明

### 后端环境变量

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `DATABASE_URL` | `postgresql://user:pass@localhost:5432/stability` | 数据库连接字符串 |
| `API_URL` | `http://127.0.0.1:8000` | 后端 API 地址 |
| `HOST_ID` | `0` | 主机 ID（Agent 使用） |
| `MOUNT_POINTS` | 空 | 挂载点列表，逗号分隔 |
| `POLL_INTERVAL` | `5` | Agent 轮询间隔（秒） |
| `ADB_PATH` | `adb` | ADB 可执行文件路径 |

### 前端代理配置

前端已配置 API 代理：
- `/api/*` → `http://localhost:8000/api/*`
- `/ws/*` → `ws://localhost:8000/ws/*`

---

## API 端点

### 主机管理
- `POST /api/v1/hosts` - 创建主机
- `GET /api/v1/hosts` - 列出主机
- `GET /api/v1/hosts/{host_id}` - 获取主机详情

### 心跳
- `POST /api/v1/heartbeat` - 接收 Agent 心跳

### 任务管理
- `GET /api/v1/task-templates` - 获取任务模板
- `POST /api/v1/tasks` - 创建任务
- `GET /api/v1/tasks/{task_id}` - 获取任务详情
- `POST /api/v1/tasks/{task_id}/dispatch` - 分发任务

### Agent 接口
- `GET /api/v1/agent/runs/pending?host_id={id}` - 获取待执行任务
- `POST /api/v1/agent/runs/{run_id}/heartbeat` - 更新任务状态
- `POST /api/v1/agent/runs/{run_id}/complete` - 完成任务

---

## 配置新的 Host 主机（Linux Agent）

### 部署步骤

1. **复制 Agent 文件到 Host 主机**

   将 `stability-test-platform\backend\agent` 中的文件转移至对应 Host 的 `/home/android/stability-agent` 文件夹中：
   ```bash
   # 使用 scp 或其他方式复制文件
   scp -r stability-test-platform/backend/agent/* android@<host-ip>:/home/android/stability-agent/
   ```

2. **在 Linux Host 上执行安装脚本**
   ```bash
   cd /home/android/stability-agent

   # 处理 Windows 换行符（如从 Windows 复制文件）
   sed -i 's/\r$//' install_agent.sh

   # 执行安装脚本
   sudo ./install_agent.sh

   # 重启服务
   sudo systemctl restart stability-test-agent
   ```

3. **验证服务状态**
   ```bash
   sudo systemctl status stability-test-agent
   ```

### 手动启动方式（开发调试用）

```bash
# 设置环境变量
export API_URL="http://<中心服务器IP>:8000"
export HOST_ID=1
export MOUNT_POINTS="/mnt/central-storage"
export ADB_PATH="/usr/bin/adb"

# 启动 Agent
cd /home/android/stability-agent
python3 -m agent.main
```

---

## 验证安装

### 1. 验证后端
```bash
curl http://localhost:8000/
```
应返回：
```json
{"message": "Stability Test Platform API", "version": "1.0.0"}
```

### 2. 验证前端
访问 `http://localhost:5173`，应看到平台界面

### 3. 测试 API
```bash
# 添加主机
curl -X POST http://localhost:8000/api/v1/hosts \
  -H "Content-Type: application/json" \
  -d "{\"name\": \"test-host\", \"ip\": \"172.21.15.10\"}"
```

### 4. 本地一致性校验（建议每次改动后执行）

```bash
# 后端语法检查
python -m compileall backend

# 前端类型检查（Node.js 20+）
cd frontend && npm run type-check
```

---

## 故障排除

### 后端无法启动
- 检查 Python 版本：`python --version`（需要 3.10+）
- 检查端口占用：`netstat -ano | findstr :8000`
- 检查防火墙设置

### 前端无法启动
- 检查 Node.js 版本：`node --version`（需要 20+）
- 删除 `node_modules` 重新安装：`rm -rf node_modules && npm install`
- 检查代理配置

### Agent 无法连接
- 检查 `API_URL` 是否正确
- 检查网络连通性：`curl http://<服务器IP>:8000/`
- 检查防火墙规则

---

## 开发模式

### 后端热重载
使用 `--reload` 参数启动，代码修改会自动重新加载。

### 前端热更新
Vite 默认支持热模块替换（HMR），修改组件后自动刷新。

---

## 生产部署

生产最小可用部署（主 Linux Host + 多 Linux Agent）请优先使用：

- `docs/production-minimum-deployment-checklist.md`
- `docs/preprod-drill-runbook.md`（预发布逐条执行）
- 控制平面模板目录：`deploy/control-plane/systemd/`、`deploy/control-plane/nginx/`、`deploy/control-plane/env/`
- Agent 安装目录：`backend/agent/`（使用 `install_agent.sh`）

仅用于快速验证的最小命令如下：

### 后端（单进程，避免重复调度）
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### 前端（构建静态文件）
```bash
npm run build
# 将 dist 目录部署到 Web 服务器
```

---

## 常见问题

**Q: 如何修改数据库？**
A: 设置 `DATABASE_URL` 环境变量，支持 PostgreSQL/MySQL。

**Q: 如何配置 HTTPS？**
A: 在 uvicorn/gunicorn 前添加反向代理（Nginx/Caddy）。

**Q: Agent 支持多台设备吗？**
A: 是的，一台主机可以连接多台 Android 设备，Agent 会并行执行任务。

---

## 联系与支持

- **项目地址**：`D:\MoveData\Users\Rin\Desktop\Stability-Tools`
- **文档**：`CLAUDE.md`
- **问题反馈**：提交 Issue 到项目仓库

---

*最后更新：2026-01-21*
