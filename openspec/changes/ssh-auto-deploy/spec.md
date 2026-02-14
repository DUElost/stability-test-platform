# SSH 自动部署功能规格说明

## 概述

实现通过 SSH MCP 连接到 Linux Agent 主机并进行自动化部署的功能。

## 技术方案

### SSH MCP 服务器

使用 `@laomeifun/mcp-ssh` 作为 SSH MCP 服务器，提供以下能力：
- SSH 命令执行
- 文件上传/下载
- 主机分组管理

### 架构

```
前端 (React)
    ↓ POST /api/v1/hosts/{id}/deploy
后端 (FastAPI)
    ↓ 调用 MCP 工具
MCP SSH Server (@laomeifun/mcp-ssh)
    ↓ SSH 连接
Linux Agent 主机
```

## 功能列表

### 后端

1. **部署 API** (`backend/api/routes/deploy.py`)
   - `POST /api/v1/hosts/{host_id}/deploy` - 触发部署
   - `GET /api/v1/hosts/{host_id}/deploy/status` - 查看部署状态
   - `GET /api/v1/hosts/{host_id}/deploy/logs` - 查看部署日志

2. **部署状态模型** (`backend/models/schemas.py`)
   - 新增 `Deployment` 表

### 前端

1. **部署按钮** - 主机详情页
2. **部署进度显示** - 实时状态
3. **部署历史列表** - 历史记录

## API 规格

### POST /api/v1/hosts/{host_id}/deploy

**请求体**:
```json
{
  "install_path": "/opt/stability-test-agent"
}
```

**响应**:
```json
{
  "deployment_id": 1,
  "host_id": 1,
  "status": "RUNNING",
  "started_at": "2026-02-13T10:00:00Z"
}
```

### GET /api/v1/hosts/{host_id}/deploy/status

**响应**:
```json
{
  "deployment_id": 1,
  "host_id": 1,
  "status": "SUCCESS",
  "started_at": "2026-02-13T10:00:00Z",
  "finished_at": "2026-02-13T10:02:00Z",
  "steps": [
    {"name": "connect", "status": "SUCCESS", "message": "SSH connected"},
    {"name": "sync_code", "status": "SUCCESS", "message": "Code synced"},
    {"name": "install", "status": "SUCCESS", "message": "Installed"},
    {"name": "start_service", "status": "SUCCESS", "message": "Service started"}
  ]
}
```

## 数据库模型

### Deployment 表

| 字段 | 类型 | 描述 |
|------|------|------|
| id | Integer | 主键 |
| host_id | Integer | 主机 ID |
| status | String | PENDING/RUNNING/SUCCESS/FAILED |
| install_path | String | 安装路径 |
| started_at | DateTime | 开始时间 |
| finished_at | DateTime | 结束时间 |
| logs | Text | 部署日志 |
| error_message | Text | 错误信息 |

## 部署流程

```
1. 获取主机 SSH 配置 (从 Host 表)
2. 映射主机名到 SSH config (host.name -> Host alias)
3. SSH 连接到目标主机
4. 创建安装目录 /opt/stability-test-agent
5. 上传 Agent 代码 (通过 MCP ssh_upload)
6. 执行安装脚本 install_agent.sh
7. 启动 systemd 服务 stability-test-agent
8. 记录部署结果
```

## SSH Config 映射

| Host 表 name | SSH Config Host |
|--------------|-----------------|
| agent-1 | agent-1 |
| agent-2 | agent-2 |

---

*Last Updated: 2026-02-13*
