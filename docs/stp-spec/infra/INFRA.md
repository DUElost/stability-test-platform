# 基础设施规范：环境变量、Docker、部署

## 1. 环境变量

### Server

```bash
# 数据库
DATABASE_URL=postgresql+asyncpg://stp:password@postgres:5432/stp

# Redis
REDIS_URL=redis://redis:6379/0

# 心跳超时配置
HEARTBEAT_TIMEOUT_SECONDS=30
HEARTBEAT_CHECK_INTERVAL_SECONDS=10

# 背压阈值
BACKPRESSURE_LAG_THRESHOLD=5000         # 触发背压的积压消息数
BACKPRESSURE_RELEASE_THRESHOLD=500      # 解除背压的积压消息数
BACKPRESSURE_LOG_RATE_LIMIT=5           # 背压时每秒最多日志条数

# 通知
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=...
JIRA_BASE_URL=https://jira.example.com
JIRA_API_TOKEN=...
JIRA_PROJECT_KEY=STP

# 工具包存储
TOOL_PACKAGE_BASE_DIR=/opt/stp/tools    # Server 侧工具包根目录
```

### Agent

```bash
# Server 连接
SERVER_URL=http://stp-server:8000
HOST_ID=host-bj-01                     # 必须全局唯一，建议用机器名

# 本地存储
SQLITE_DB_PATH=/var/stp/agent.db
TOOL_REGISTRY_PATH=/opt/stp/tools      # 本地工具包根目录

# 资源配额
AGENT_CPU_QUOTA=2                      # 最大并行 CPU 密集型进程数

# 轮询间隔
JOB_POLL_INTERVAL_SECONDS=5
HEARTBEAT_INTERVAL_SECONDS=10
```

## 2. Docker Compose（开发环境）

```yaml
# docker-compose.yml
version: "3.9"

services:
  postgres:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: stp
      POSTGRES_USER: stp
      POSTGRES_PASSWORD: password
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
    ports:
      - "6379:6379"

  server:
    build: ./server
    env_file: .env.server
    depends_on:
      - postgres
      - redis
    ports:
      - "8000:8000"
    volumes:
      - ./server:/app
      - tool_packages:/opt/stp/tools

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
    environment:
      - VITE_API_BASE_URL=http://localhost:8000
      - VITE_WS_BASE_URL=ws://localhost:8000

  # Agent 在实际部署中运行在各 Host 机器上
  # 开发环境可以启动一个模拟 Agent
  agent-dev:
    build: ./agent
    env_file: .env.agent.dev
    depends_on:
      - server
    volumes:
      - ./agent:/app

volumes:
  postgres_data:
  tool_packages:
```

## 3. 启动顺序

```bash
# 1. 启动基础设施
docker compose up -d postgres redis

# 2. 执行数据库迁移
docker compose run --rm server alembic upgrade head

# 3. 启动 Server
docker compose up -d server

# 4. 启动前端（开发模式）
cd frontend && npm run dev

# 5. 启动开发 Agent
docker compose up -d agent-dev
```

## 4. Redis 内存策略

```
maxmemory:         512mb
maxmemory-policy:  allkeys-lru

说明：
- stp:status 使用 MAXLEN 100000，超出自动截断旧消息
- stp:logs 使用 MAXLEN 500000
- 生产环境建议调高 maxmemory 至 2gb
```

## 5. 生产部署注意事项

**PostgreSQL**：
- 开启连接池（建议 PgBouncer）
- `step_trace` 表增长较快，建议按月分区（`PARTITION BY RANGE (created_at)`）

**Redis**：
- 启用 AOF 持久化，防止 Redis 重启后 PEL 丢失导致消息未被消费
- 配置：`appendonly yes`, `appendfsync everysec`

**Agent 部署**：
- 每台 Host 机器上运行一个 Agent 进程
- 建议用 systemd 管理，确保崩溃后自动重启
- SQLite 文件建议放在 SSD 路径，防止磁盘 I/O 成为瓶颈

```ini
# /etc/systemd/system/stp-agent.service
[Unit]
Description=STP Host Agent
After=network.target

[Service]
Type=simple
User=stp
WorkingDirectory=/opt/stp/agent
EnvironmentFile=/etc/stp/agent.env
ExecStart=/opt/stp/venv/bin/python main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
