# Linux Agent Host Ansible 运维

正式运维记录文档见：

- `docs/linux-agent-ansible-runbook.md`

## 前提

- 在 WSL 中执行 Ansible
- 使用 `tools/ansible/inventory.ini` 中的 `android` 账号连接目标主机
- `inventory.ini` 中的登录密码当前同时作为 sudo 密码使用

## 首次配置

1. **inventory**：仓库不再保存真实凭据。
   ```bash
   cp tools/ansible/inventory.example.ini tools/ansible/inventory.ini
   # 然后填入各主机真实 ansible_password（或在 SSH 公钥认证就绪后删除该字段）
   ```
   `tools/ansible/inventory.ini` 已在仓库根 `.gitignore` 中排除，不会被提交。

2. **AGENT_SECRET（Ansible Vault）**：仓库内 `group_vars/linux_hosts.vault.yml` 默认是占位文件，必须先加密 + 填入真实值后才能跑 install/update playbook。
   ```bash
   # 选择一个本地 vault 密码文件（建议放在仓库外，例如 ~/.ansible/vault_pass.txt，并 chmod 600）
   export ANSIBLE_VAULT_PASSWORD_FILE=~/.ansible/vault_pass.txt

   # 加密：
   ansible-vault encrypt tools/ansible/group_vars/linux_hosts.vault.yml

   # 编辑（填入真实 vault_agent_secret，长度 ≥ 16）：
   ansible-vault edit tools/ansible/group_vars/linux_hosts.vault.yml
   ```
   全部 Linux Agent 主机共用同一个 `AGENT_SECRET`。配置一次后，后续部署/热更新会自动从 vault 读取。
   未注入 vault 或仍使用占位值时，install/update playbook 顶部断言会失败，终止执行。

3. **SSH 公钥分发**（可选，用于切换到密钥认证）：
   ```bash
   ansible-playbook -i tools/ansible/inventory.ini tools/ansible/ssh_key.yml
   ```

## 常用命令

### 连通性检查

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o'
```

### 单机首次部署

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36"
```

### 单机热更新

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36"
```

### 灰度热更新（推荐）

`update_agent.yml` 已配置 `serial: "20%"` + `any_errors_fatal: true`，单批失败立即停止；仍建议先打金丝雀：

```bash
# 1. 灰度组先行
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit agent_canary"

# 2. 观察 agentctl health 与 dashboard 5-10 分钟，无异常后推全量：
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit agent_prod"
```

> **回滚机制**：每次同步代码前 playbook 会先 `rsync -a` 备份当前 `agent/` 目录到 `/opt/stability-test-agent/agent.bak.<ts>/`。
> 如果 `agentctl health` 失败，`rescue` 块会自动回滚并抛错，让批次停在当前金丝雀。

### 开发环境切换 API 地址并批量回写

当 Windows 开发机 IP 变化时，不需要重装 agent。直接覆盖 `agent_api_url` 并批量执行热更新即可，playbook 会同步回写远端 `/opt/stability-test-agent/.env` 中的 `API_URL`。

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://172.21.10.13:8000"
```

更新后建议立即批量检查：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml"
```

如果只是想修改默认值，再编辑 `tools/ansible/group_vars/linux_hosts.yml` 中的 `agent_api_url`。

### 单机服务重启

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart"
```

### 单机状态检查

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/tools/ansible && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```

## 故障排查

- **断言失败：agent_secret 未注入或仍为占位值**
  - 确认 `linux_hosts.vault.yml` 已加密、`vault_agent_secret` 已填真值。
  - 确认 `ANSIBLE_VAULT_PASSWORD_FILE` 指向正确的本地 vault 密码文件。

- **update_agent 因 health 失败回滚**
  - 检查 `/opt/stability-test-agent/logs/agent_error.log` 与 `agentctl health` 输出。
  - 备份目录 `/opt/stability-test-agent/agent.bak.<ts>/` 可手工进一步比对差异。
  - 修复后重新执行 `ansible-playbook playbooks/update_agent.yml --limit <host>`。

- **批次因 `any_errors_fatal: true` 中止**
  - 修复失败主机后，使用 `--limit` 单独重试该主机；下一批不会自动继续。
