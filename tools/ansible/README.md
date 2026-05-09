# Linux Agent Host Ansible 运维

正式运维记录文档见：

- `docs/linux-agent-ansible-runbook.md`

## 前提

- 在 WSL 的普通用户下执行 Ansible，**不要使用 `wsl -u root`**。
- 使用 `tools/ansible/inventory.ini` 中的 `android` 账号连接目标主机。
- `inventory.ini` 中的登录密码当前同时作为 sudo 密码使用。
- `AGENT_SECRET` 必须与后端 `.env` 中的 `AGENT_SECRET` 保持一致。

## 一次性环境准备

每个运维同学只需在本机配置一次。

### 1. 准备 inventory

仓库不再保存真实主机密码，首次使用先复制示例文件：

```bash
cd /mnt/f/stability-test-platform/tools/ansible
cp inventory.example.ini inventory.ini
```

然后编辑 `inventory.ini`，把每行 `ansible_password=__SET_LOCALLY__` 改成对应主机的真实密码。

`tools/ansible/inventory.ini` 已在仓库根 `.gitignore` 中排除，不会被提交。

### 2. 配置全局 AGENT_SECRET

全部 Linux Agent 主机默认共用后端 `backend/.env` 中的 `AGENT_SECRET`。
测试环境允许使用后端 `.env` 中的默认值；正式环境建议通过环境变量或 Vault 覆盖为真实值。
`install_agent.yml` / `update_agent.yml` 会按以下顺序读取：

1. 有效的 `vault_agent_secret`
2. 当前 shell 环境变量 `AGENT_SECRET`
3. 本仓库 `backend/.env` 中的 `AGENT_SECRET`

通常本地测试环境无需额外配置，只要 `backend/.env` 已有有效 `AGENT_SECRET` 即可。
如果要在正式环境覆盖，可使用环境变量：

```bash
export AGENT_SECRET="<与后端一致且长度至少 16 的 AGENT_SECRET>"
```

也可以使用 Ansible Vault 管理 `vault_agent_secret`，但不是日常测试同步的必需步骤。

### 3. 设置默认工作目录（推荐）

后续命令都建议在 Ansible 目录下执行：

```bash
cd /mnt/f/stability-test-platform/tools/ansible
export ANSIBLE_CONFIG=./ansible.cfg
```

## 日常命令

### 连通性检查

```bash
ansible -i inventory.ini linux_hosts -m ping -o
```

从 Windows 终端直接执行：

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o'
```

### 单机状态检查

```bash
ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36
```

从 Windows 终端直接执行：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```

### 单机服务管理

```bash
# 重启
ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart

# 启动
ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=start

# 停止
ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=stop

# 查看状态
ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=status
```

## 首次部署

### 单机首次部署

```bash
ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36
```

从 Windows 终端直接执行：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36"
```

部署完成后，playbook 会自动验证：

1. `systemctl is-active stability-test-agent` 返回 `active`。
2. `/opt/stability-test-agent/agentctl health` 返回 0。

任一验证失败，部署会失败退出。

## 热更新

### 推荐流程：先金丝雀，再全量

`update_agent.yml` 已配置：

- `serial: "20%"`：分批更新。
- `max_fail_percentage: 0`：任何失败都视为批次失败。
- `any_errors_fatal: true`：单批失败立即停止，不继续推下一批。
- 本地 `rsync --dry-run --itemize-changes` 先比对正式目录，有差异才同步。
- 无代码、`agentctl`、环境变量差异时跳过备份、同步和重启。
- 自动备份 + 失败回滚。

控制端需要安装 `rsync`；使用密码登录的节点还需要安装 `sshpass`。

推荐发布顺序：

```bash
# 1. 先更新金丝雀组
ansible-playbook playbooks/update_agent.yml --limit agent_canary

# 2. 观察 5-10 分钟：agentctl health、Dashboard 心跳、日志、Job 执行状态

# 3. 无异常后更新生产组
ansible-playbook playbooks/update_agent.yml --limit agent_prod
```

从 Windows 终端直接执行：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit agent_canary"
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit agent_prod"
```

### 单机热更新

```bash
ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36
```

从 Windows 终端直接执行：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36"
```

### 自动回滚机制

检测到代码差异时，playbook 会先备份当前目录：

```text
/opt/stability-test-agent/agent/ -> /opt/stability-test-agent/agent.bak.<timestamp>/
```

检测到 `agentctl` 差异时，会额外备份：

```text
/opt/stability-test-agent/agentctl -> /opt/stability-test-agent/agentctl.bak.<timestamp>
```

如果更新后出现以下任一失败：

1. `systemctl is-active stability-test-agent` 未返回 `active`。
2. `/opt/stability-test-agent/agentctl health` 返回非 0。

playbook 会自动执行 rescue：

1. 用 `agent.bak.<timestamp>/` 回滚 `/opt/stability-test-agent/agent/`。
2. 如本次更新过 `agentctl`，用 `agentctl.bak.<timestamp>` 回滚 `/opt/stability-test-agent/agentctl`。
3. 重启 `stability-test-agent`。
4. 抛错终止当前批次。

默认只保留最近 1 份 `agent.bak.*` 和 `agentctl.bak.*` 备份。

## 切换 API 地址

当 Windows 开发机 IP 变化时，不需要重装 Agent。直接覆盖 `agent_api_url` 并执行热更新即可，playbook 会同步回写远端 `/opt/stability-test-agent/.env` 中的 `API_URL`。

### 灰度切换

```bash
ansible-playbook playbooks/update_agent.yml \
  -e agent_api_url=http://172.21.10.13:8000 \
  --limit agent_canary

ansible-playbook playbooks/check_agent.yml --limit agent_canary

ansible-playbook playbooks/update_agent.yml \
  -e agent_api_url=http://172.21.10.13:8000 \
  --limit agent_prod
```

### 全量切换

```bash
ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://172.21.10.13:8000
ansible-playbook playbooks/check_agent.yml
```

如果只是想修改默认值，编辑 `tools/ansible/group_vars/linux_hosts.yml` 中的 `agent_api_url`。

## SSH 公钥分发（可选）

用于后续从密码认证切换到密钥认证：

```bash
ansible-playbook -i inventory.ini ssh_key.yml
```

完成后可逐步在 `inventory.ini` 中删除 `ansible_password`，改用 SSH key 登录。

## inventory 分组建议

`inventory.example.ini` 提供以下分组：

- `agent_canary`：金丝雀主机，建议放 1-2 台稳定、容易观察的机器。
- `agent_prod`：其余生产主机。
- `linux_hosts`：`agent_canary` + `agent_prod` 的并集。

批量发布建议始终按以下顺序：

```bash
ansible-playbook playbooks/update_agent.yml --limit agent_canary
# 观察 5-10 分钟
ansible-playbook playbooks/update_agent.yml --limit agent_prod
```

## 故障排查

### `agent_secret | length >= 16` 断言失败

原因：未能从 `vault_agent_secret`、环境变量 `AGENT_SECRET` 或 `backend/.env` 读取到有效密钥，或密钥长度不足 16。

处理：确认 `backend/.env` 中存在有效 `AGENT_SECRET`，或执行前设置环境变量：

```bash
export AGENT_SECRET="<与后端一致且长度至少 16 的 AGENT_SECRET>"
```

### `agent_secret` 仍为占位值

原因：传入的 `vault_agent_secret` / `AGENT_SECRET` 仍是：

```text
REPLACE_WITH_VAULTED_SECRET_AT_LEAST_16_CHARS
```

处理：删除占位覆盖值，或改为与后端一致的真实值。日常测试环境优先使用 `backend/.env`。

### update_agent 因 health 失败回滚

处理步骤：

1. 登录失败主机。
2. 查看：
   ```bash
   /opt/stability-test-agent/agentctl health
   tail -n 80 /opt/stability-test-agent/logs/agent_error.log
   ```
3. 必要时对比备份目录：
   ```bash
   ls -ld /opt/stability-test-agent/agent.bak.*
   ```
4. 修复后单机重跑：
   ```bash
   ansible-playbook playbooks/update_agent.yml --limit <host>
   ```

### 批次因 `any_errors_fatal: true` 中止

这是预期行为。修复失败主机后，先单独重试失败主机：

```bash
ansible-playbook playbooks/update_agent.yml --limit <failed-host>
```

确认成功后，再继续原来的 `agent_canary` 或 `agent_prod` 更新流程。
