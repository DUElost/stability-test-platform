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

### 2. 准备 Ansible Vault

创建本地 vault 密码文件，建议放在仓库外：

```bash
mkdir -p ~/.ansible
printf '%s\n' '<你的-vault-密码>' > ~/.ansible/vault_pass.txt
chmod 600 ~/.ansible/vault_pass.txt
```

让当前 shell 使用该 vault 密码文件：

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible/vault_pass.txt
```

如需长期生效，可写入 `~/.bashrc`：

```bash
echo 'export ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible/vault_pass.txt' >> ~/.bashrc
source ~/.bashrc
```

### 3. 配置全局 AGENT_SECRET

全部 Linux Agent 主机共用一个 `AGENT_SECRET`。配置一次后，后续首次部署和热更新会自动从 vault 读取。

```bash
cd /mnt/f/stability-test-platform/tools/ansible
ansible-vault encrypt group_vars/linux_hosts.vault.yml
ansible-vault edit group_vars/linux_hosts.vault.yml
```

在 vault 文件中填入真实值：

```yaml
vault_agent_secret: "<与后端 .env 一致且长度至少 16 的 AGENT_SECRET>"
```

如果未注入 vault、仍是占位值，或长度小于 16，`install_agent.yml` / `update_agent.yml` 会在第一步断言失败并终止。

### 4. 设置默认工作目录（推荐）

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
- 自动备份 + 失败回滚。

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

每次同步代码前，playbook 会先备份当前目录：

```text
/opt/stability-test-agent/agent/ -> /opt/stability-test-agent/agent.bak.<timestamp>/
```

如果更新后出现以下任一失败：

1. `systemctl is-active stability-test-agent` 未返回 `active`。
2. `/opt/stability-test-agent/agentctl health` 返回非 0。

playbook 会自动执行 rescue：

1. 用 `agent.bak.<timestamp>/` 回滚 `/opt/stability-test-agent/agent/`。
2. 重启 `stability-test-agent`。
3. 抛错终止当前批次。

默认只保留最近 1 份 `agent.bak.*` 备份。

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

### `Mandatory variable 'vault_agent_secret' not defined`

原因：没有注入 Ansible Vault 变量。

处理：

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible/vault_pass.txt
ansible-vault edit group_vars/linux_hosts.vault.yml
```

确认 `vault_agent_secret` 已填入真实值。

### `agent_secret | length >= 16` 断言失败

原因：`vault_agent_secret` 长度不足 16。

处理：把 `vault_agent_secret` 改成至少 16 字符，并确保与后端 `.env` 中 `AGENT_SECRET` 一致。

### `agent_secret` 仍为占位值

原因：vault 文件中仍是：

```yaml
vault_agent_secret: "REPLACE_WITH_VAULTED_SECRET_AT_LEAST_16_CHARS"
```

处理：用 `ansible-vault edit group_vars/linux_hosts.vault.yml` 改为真实值。

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
