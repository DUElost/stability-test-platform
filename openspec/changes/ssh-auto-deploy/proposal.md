  # SSH 自动部署功能需求规范

## 1. 上下文

用户需要在稳定性测试管理平台中实现**自动部署功能**：通过 SSH MCP 连接到 Linux Agent 主机并进行自动化部署。

### 现有基础设施

- **SSH MCP**: 使用 `@laomeifun/mcp-ssh` MCP 服务器
- **SSH 配置**: 使用 `~/.ssh/config` 管理主机连接配置
- **主机模型**: `Host` 表已包含 `ssh_port`, `ssh_user`, `ssh_auth_type`, `ssh_key_path` 字段
- **部署脚本**: `backend/agent/install_agent.sh` (Linux 安装脚本)
- **手动脚本**: `scripts/sync-agent.bat`, `scripts/run-cmd.bat` 等

### 用户确认需求

1. **部署步骤**: 完整部署（同步代码 + 安装依赖 + 启动服务 + 配置 systemd）
2. **触发方式**: 手动触发（用户通过 UI 按钮触发）
3. **安装路径**: 固定路径 `/opt/stability-test-agent`

---

## 2. 需求

### REQ-001: 部署服务 API

**场景**: 用户通过前端页面点击"部署"按钮，触发对目标主机的完整部署流程

**描述**:
- 在后端创建 `/api/v1/hosts/{host_id}/deploy` 端点
- 支持同步执行（返回部署结果）和异步执行（返回部署任务ID）
- 部署流程：
  1. 通过 SSH MCP 连接目标主机
  2. 创建目录 `/opt/stability-test-agent`
  3. 同步 Agent 代码（通过 SFTP）
  4. 执行安装脚本 `install_agent.sh`
  5. 启动 systemd 服务 `stability-test-agent`
- 部署过程记录日志，支持实时查看

**验收标准**:
- [ ] API 端点返回 200 表示部署成功
- [ ] API 端点返回错误码和消息表示部署失败
- [ ] 部署日志可在前端查看

---

### REQ-002: SSH MCP 集成

**场景**: 使用 MCP SSH 服务器进行 SSH 连接

**描述**:
- 配置 `@laomeifun/mcp-ssh` MCP 服务器
- 在 `~/.ssh/config` 中配置主机连接信息
- 使用 MCP 工具执行远程命令：
  - `ssh_command` - 执行远程命令
  - `ssh_upload` - 上传文件
  - `ssh_download` - 下载文件
- 支持密钥认证和密码认证

**验收标准**:
- [ ] MCP SSH 服务器正常启动
- [ ] 可通过主机名连接 SSH
- [ ] 可执行远程命令
- [ ] 可上传/下载文件

---

### REQ-003: 部署状态跟踪

**场景**: 用户需要了解部署进度和结果

**描述**:
- 创建 `Deployment` 表记录部署历史
- 状态：PENDING, RUNNING, SUCCESS, FAILED
- 记录部署开始时间、结束时间、错误信息

**验收标准**:
- [ ] 可查询主机的部署历史
- [ ] 可查看每次部署的详细日志

---

### REQ-004: 前端部署界面

**场景**: 用户通过 UI 触发部署

**描述**:
- 在主机详情页添加"部署"按钮
- 显示部署进度和结果
- 显示部署历史列表

**验收标准**:
- [ ] 点击部署按钮触发部署
- [ ] 显示部署状态（进行中/成功/失败）
- [ ] 可查看部署日志

---

## 3. 成功判据

### 3.1 功能验证

| 判据 | 验证方法 |
|------|----------|
| API 可接受部署请求 | POST /api/v1/hosts/1/deploy 返回 200 |
| SSH 连接成功 | 部署日志显示 "SSH connected" |
| 代码同步成功 | 部署日志显示 "Code synced" |
| 服务启动成功 | systemctl status stability-test-agent 返回 active |
| 主机状态更新 | 主机心跳报告显示 agent 在线 |

### 3.2 错误处理

| 场景 | 预期行为 |
|------|----------|
| SSH 连接失败 | 返回错误信息 "SSH connection failed: ..." |
| 认证失败 | 返回错误信息 "Authentication failed" |
| 安装脚本失败 | 返回错误信息 "Installation failed: ..." |
| 服务启动失败 | 返回错误信息 "Service start failed: ..." |

---

## 4. 技术约束

### 4.1 硬约束

- 安装路径固定为 `/opt/stability-test-agent`
- 使用 `@laomeifun/mcp-ssh` MCP 服务器
- 主机连接配置在 `~/.ssh/config` 中管理
- 复用现有 Host 模型的 SSH 配置字段

### 4.2 软约束

- 部署操作应在 5 分钟内完成
- 支持同时部署到多个主机

---

## 5. SSH MCP 配置

### 5.1 安装

```bash
npm install -g @laomeifun/mcp-ssh
```

### 5.2 SSH Config 配置

在 `~/.ssh/config` 中添加主机配置：

```
Host agent-1
    HostName 172.21.15.1
    User android
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no

Host agent-2
    HostName 172.21.15.2
    User android
    Port 22
    IdentityFile ~/.ssh/id_ed25519
    StrictHostKeyChecking no
```

### 5.3 MCP 工具

| 工具 | 功能 |
|------|------|
| ssh_command | 在远程主机执行命令 |
| ssh_upload | 上传文件到远程主机 |
| ssh_download | 从远程主机下载文件 |

---

## 6. 依赖

### 6.1 内部依赖

- `backend/models/schemas.py` - Host 模型
- `backend/api/routes/hosts.py` - 主机 API
- `backend/agent/install_agent.sh` - Agent 安装脚本

### 6.2 外部依赖

- `@laomeifun/mcp-ssh` - SSH MCP 服务器

---

## 7. 实施顺序

1. 配置 SSH MCP 服务器
2. 配置 SSH config 主机
3. 实现部署 API 端点
4. 实现部署状态跟踪（数据库模型）
5. 实现前端部署界面

---

*Created: 2026-02-13*
*Change: ssh-auto-deploy*
