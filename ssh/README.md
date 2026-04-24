# Linux Agent Host Ansible 运维

正式运维记录文档见：

- `docs/linux-agent-ansible-runbook.md`

## 前提

- 在 WSL 中执行 Ansible
- 使用 `ssh/inventory.ini` 中的 `android` 账号连接目标主机
- `inventory.ini` 中的登录密码当前同时作为 sudo 密码使用

## 常用命令

### 连通性检查

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o'
```

### 单机首次部署

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36"
```

### 单机热更新

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36"
```

### 开发环境切换 API 地址并批量回写

当 Windows 开发机 IP 变化时，不需要重装 agent。直接覆盖 `agent_api_url` 并批量执行热更新即可，playbook 会同步回写远端 `/opt/stability-test-agent/.env` 中的 `API_URL`。

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://172.21.10.13:8000"
```

更新后建议立即批量检查：

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml"
```

如果只是想修改默认值，再编辑 `ssh/group_vars/linux_hosts.yml` 中的 `agent_api_url`。

### 单机服务重启

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart"
```

### 单机状态检查

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```
