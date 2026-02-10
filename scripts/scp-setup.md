# SCP 配置指南 - Windows 到 Linux 同步

本文档介绍如何配置 SCP 以便从 Windows 直接修改 Linux 主机的文件。

---

## 方案概览

### 推荐方案对比

| 方案 | 优点 | 缺点 | 推荐度 |
|------|------|------|--------|
| **VS Code Remote SSH** | 直接编辑、无需手动同步 | 需要安装插件 | ⭐⭐⭐⭐⭐ |
| **SSH 密钥 + SCP** | 安全、可脚本化 | 需要初始配置 | ⭐⭐⭐⭐ |
| **OpenSSH (Win10/11 内置)** | 无需安装工具 | 命令行操作 | ⭐⭐⭐⭐ |
| **Xftp / WinSCP** | 图形界面 | 需要手动操作 | ⭐⭐⭐ |

---

## 方案一：VS Code Remote SSH（推荐）

### 安装插件

1. 安装 VS Code
2. 安装 **Remote - SSH** 插件

### 配置步骤

1. **生成 SSH 密钥对**（在 Windows 上）：
   ```powershell
   # 打开 PowerShell
   ssh-keygen -t ed25519 -C "your_email@example.com"
   # 一路回车，使用默认路径
   ```

2. **复制公钥到 Linux 主机**：
   ```powershell
   # 方式 1：手动复制
   type C:\Users\YourName\.ssh\id_ed25519.pub
   # 复制输出内容

   # 方式 2：使用 scp-copy 脚本（见下方）
   ```

3. **在 Linux 主机上添加公钥**：
   ```bash
   # 在 Linux 主机上执行
   mkdir -p ~/.ssh
   chmod 700 ~/.ssh
   echo "你的公钥内容" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```

4. **配置 VS Code SSH**：
   - 创建/编辑 `C:\Users\YourName\.ssh\config`：
   ```
   Host linux-agent-1
       HostName 172.21.15.101
       User root
       IdentityFile C:\Users\YourName\.ssh\id_ed25519

   Host linux-agent-2
       HostName 172.21.15.102
       User root
       IdentityFile C:\Users\YourName\.ssh\id_ed25519
   ```

5. **连接并编辑**：
   - 按 `Ctrl+Shift+P`
   - 输入 `Remote-SSH: Connect to Host`
   - 选择主机
   - 直接编辑文件，保存自动同步

---

## 方案二：OpenSSH + 批处理脚本

### 检查 Windows OpenSSH

```powershell
# 检查是否已安装
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH*'

# 如需安装
Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
```

### 密钥认证配置

1. **生成密钥**：
   ```powershell
   ssh-keygen -t ed25519 -f C:\Users\YourName\.ssh\stability_test_key
   ```

2. **复制公钥到 Linux**：
   ```powershell
   # 手动复制
   type C:\Users\YourName\.ssh\stability_test_key.pub

   # 或使用本项目的 copy-key.ps1 脚本
   .\scripts\copy-key.ps1 172.21.15.101
   ```

3. **配置 SSH Config**：
   ```
   # C:\Users\YourName\.ssh\config
   Host stability-*
       User root
       IdentityFile C:\Users\YourName\.ssh\stability_test_key
       StrictHostKeyChecking no
   ```

---

## 方案三：自动同步脚本

### sync-to-linux.ps1

一键同步 Agent 代码到 Linux 主机：

```powershell
# 使用方式
.\scripts\sync-to-linux.ps1 -Host 172.21.15.101

# 同步到所有主机
.\scripts\sync-to-linux.ps1 -AllHosts
```

### sync-to-linux.bat

Windows 批处理版本：

```cmd
# 使用方式
sync-to-linux.bat 172.21.15.101
```

---

## 快速开始检查清单

- [ ] Windows 已安装 OpenSSH Client
- [ ] 已生成 SSH 密钥对
- [ ] 公钥已复制到 Linux 主机
- [ ] SSH 连接测试成功
- [ ] SCP 文件传输测试成功

---

## 故障排查

### 问题：Permission Denied

```bash
# 检查 Linux 主机 SSH 配置
sudo nano /etc/ssh/sshd_config
# 确保以下配置：
# PubkeyAuthentication yes
# PasswordAuthentication yes（临时开启，配置密钥后可关闭）

# 重启 SSH 服务
sudo systemctl restart sshd
```

### 问题：Host Key Verification Failed

```powershell
# 清除已知主机
ssh-keygen -R 172.21.15.101

# 或在 SSH Config 中添加：
StrictHostKeyChecking no
UserKnownHostsFile=/dev/null
```

---

## 目录映射

| Windows 路径 | Linux 路径 |
|-------------|-----------|
| `D:\...\backend\agent\` | `/opt/stability-test-agent/agent/` |
| `D:\...\backend\requirements.txt` | `/opt/stability-test-agent/requirements.txt` |

---

*文档版本: 1.0.0*
*最后更新: 2026-01-24*
