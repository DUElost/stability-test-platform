# SCP 同步脚本使用指南

本目录包含从 Windows 同步代码到 Linux Agent 主机的脚本和配置。

---

## 快速开始

### 自动配置（推荐）

双击运行配置脚本：

```cmd
setup-scp.bat
```

此脚本将自动完成：
1. 检查/安装 OpenSSH Client
2. 生成 SSH 密钥对
3. 配置 SSH Config
4. 引导复制公钥到 Linux

---

## 脚本说明

### setup-scp.bat
一键配置 SCP 环境。首次使用请运行此脚本。

### sync-to-linux.bat
批处理版本的同步脚本。

```cmd
# 同步到指定主机
sync-to-linux.bat 172.21.15.101

# 使用别名 (需先配置 SSH Config)
sync-to-linux.bat agent1

# 同步到所有主机
sync-to-linux.bat all

# 同步后重启 Agent 服务
sync-to-linux.bat 172.21.15.101 restart
```

### sync-to-linux.ps1
PowerShell 版本的同步脚本，功能更丰富。

```powershell
# 同步到指定主机
.\sync-to-linux.ps1 -Host 172.21.15.101

# 同步到所有主机
.\sync-to-linux.ps1 -AllHosts

# 同步后重启服务
.\sync-to-linux.ps1 -Host 172.21.15.101 -Restart

# 指定密钥文件
.\sync-to-linux.ps1 -Host 172.21.15.101 -IdentityFile C:\Users\YourName\.ssh\custom_key
```

### copy-key.ps1
复制 SSH 公钥到 Linux 主机，实现无密码登录。

```powershell
# 复制公钥到指定主机
.\copy-key.ps1 172.21.15.101

# 指定用户名
.\copy-key.ps1 172.21.15.101 -User username
```

---

## 配置文件

### ssh-config-example.txt
SSH 配置文件示例。复制到 `C:\Users\YourName\.ssh\config` 并修改。

配置后可以使用别名连接主机：
- `ssh linux-agent-1` 代替 `ssh root@172.21.15.101`
- `scp file.txt agent1:/tmp/` 代替 `scp file.txt root@172.21.15.101:/tmp/`

---

## VS Code Remote SSH（推荐开发方式）

### 安装插件
1. 打开 VS Code
2. 按 `Ctrl+Shift+X` 打开扩展面板
3. 搜索并安装 **Remote - SSH**

### 连接步骤
1. 按 `Ctrl+Shift+P`
2. 输入 `Remote-SSH: Connect to Host`
3. 选择配置的主机 (agent1, agent2, etc.)
4. 打开 `/opt/stability-test-agent/` 目录
5. 直接编辑文件，保存自动同步

---

## 常见问题

### Q: 提示 "ssh 不是内部或外部命令"

**A:** Windows 需要安装 OpenSSH Client。
```powershell
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### Q: 提示 "Permission Denied (publickey)"

**A:** 公钥未正确复制到 Linux 主机。运行：
```powershell
.\copy-key.ps1 172.21.15.101
```

### Q: 提示 "Host Key Verification Failed"

**A:** 清除已知主机缓存：
```cmd
ssh-keygen -R 172.21.15.101
```

或在 SSH Config 中添加：
```
StrictHostKeyChecking no
```

### Q: 同步后修改不生效

**A:** 需要重启 Agent 服务：
```cmd
.\sync-to-linux.bat 172.21.15.101 restart
```

或在 Linux 上手动重启：
```bash
sudo systemctl restart stability-test-agent
```

---

## 目录映射

| Windows 源路径 | Linux 目标路径 |
|---------------|---------------|
| `backend/agent/` | `/opt/stability-test-agent/agent/` |
| `backend/requirements.txt` | `/opt/stability-test-agent/requirements.txt` |

---

## 安全建议

1. **生产环境**: 建议使用专用密钥，禁用密码登录
2. **密钥权限**: Windows 上无需特殊权限，Linux 上必须 `chmod 600`
3. **SSH Config**: 首次连接后，建议启用 `StrictHostKeyChecking`
4. **密钥备份**: 备份私钥到安全位置

---

*最后更新: 2026-01-24*
