# Linux Agent Ansible 运维 Runbook

## 1. 文档范围

本文档用于记录当前 Linux agent host 的 Ansible 运维方案（Phase A）。

适用范围：

- 首次部署
- 热更新
- 服务启停与重启
- 状态检查
- 开发环境 API 地址变更后的批量回写

当前实现仍属于过渡方案：

- 首次部署复用 `backend/agent/install_agent.sh`
- 日常运维入口统一收敛到 `ssh/playbooks/*.yml`
- 后续 Phase B 再迁移到完全原生的 Ansible tasks

## 2. 目录与入口

当前实现位于主仓库：

- 仓库根目录：`F:\stability-test-platform`
- Ansible 入口目录：`ssh/`

关键文件：

- `ssh/ansible.cfg`：Ansible 默认配置
- `ssh/inventory.ini`：主机清单，本地敏感文件，不纳入版本控制
- `ssh/group_vars/linux_hosts.yml`：Linux 主机默认变量
- `ssh/playbooks/install_agent.yml`：首次部署
- `ssh/playbooks/update_agent.yml`：热更新
- `ssh/playbooks/service_agent.yml`：服务管理
- `ssh/playbooks/check_agent.yml`：状态检查
- `ssh/roles/agent_deploy/defaults/main.yml`：服务动作白名单等默认值

建议在 WSL 中先定义仓库根路径：

```bash
export REPO_ROOT=/mnt/f/stability-test-platform
cd "$REPO_ROOT/ssh"
```

## 3. 执行前提

- 在 Windows 上通过 `wsl bash -lc` 执行 Ansible
- 远端连接用户为 `android`
- Ansible 使用 `sudo/become` 提权
- 当前默认将 `inventory.ini` 中的 `ansible_password` 同时作为 `ansible_become_password`
- 目标主机需已具备 Python、sudo、systemd 基础环境

当前 `ansible.cfg` 默认约定：

- `inventory = ./inventory.ini`
- `remote_user = android`
- `roles_path = ./roles`
- `host_key_checking = False`

## 4. 变量模型

默认变量来源于 `ssh/group_vars/linux_hosts.yml`。

关键变量：

- `agent_api_url`：agent 上报到的平台地址
- `agent_install_dir`：默认 `/opt/stability-test-agent`
- `agent_service_name`：默认 `stability-test-agent`
- `agent_remote_tmp_dir`：远端暂存目录
- `agent_user` / `agent_group`：部署目录属主，默认 `android`
- `agent_check_strict`：`check_agent.yml` 是否严格失败
- `agent_status_require_active`：`service_agent.yml status` 是否要求服务必须为 `active`
- `agent_health_log_tail_lines`：检查时拉取的错误日志尾部行数

覆盖规则：

- 默认值放在 `group_vars/linux_hosts.yml`
- 当前不再保留 `172.21.10.36` 的单独 `host_vars`
- 开发环境临时切换 API 地址时，优先使用 `-e agent_api_url=...` 覆盖，不必改文件

## 5. Playbook 职责

### `install_agent.yml`

职责：

- 创建远端暂存目录
- 拷贝 `backend/agent/` 到目标机
- 统一修正 CRLF
- 非交互执行 `install_agent.sh`
- 刷新 `agentctl`
- 修正安装目录和 `.env` 权限
- 启用并启动 systemd 服务
- 等待服务达到 `active`

注意：

- 首次部署会保留安装脚本的现有行为
- `.env` 仍由安装脚本生成
- 当前不做失败后的破坏性回滚

### `update_agent.yml`

职责：

- 校验安装目录和 `.env` 已存在
- 同步最新 agent 代码到已安装目录
- 刷新 `agentctl`
- 回写远端 `.env` 中的 `API_URL`
- `daemon-reload` + `restart`
- 输出错误日志尾部

注意：

- 该 playbook 不重建安装目录
- 当前只自动回写 `API_URL`，不会把整份 `.env` 模板化重建

### `service_agent.yml`

职责：

- 支持 `start` / `stop` / `restart` / `status`
- 在执行前先断言目标机已安装
- 对结果做最终状态断言，避免未安装主机误判为成功

### `check_agent.yml`

职责：

- `ansible ping`
- 检查安装目录
- 检查 `.env`
- 检查 systemd 服务状态
- 执行 `agentctl health`
- 输出错误日志尾部
- 在严格模式下对未安装、未配置、服务未运行、健康检查失败直接报错

## 6. 常用命令

### 连通性检查

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o'
```

### 单机首次部署

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36'
```

### 单机热更新

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36'
```

### 单机状态检查

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36'
```

### 单机重启服务

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart'
```

### 单机查看服务状态

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=status'
```

### 批量首次部署

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml'
```

### 批量热更新

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml'
```

### 批量服务重启

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml -e agent_service_action=restart'
```

### 批量状态检查

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml'
```

### 只操作部分主机

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit "172.21.10.36,172.21.15.7,172.21.15.2"'
```

## 7. 开发环境 IP 变化时的标准流程

开发环境下 Windows 平台 IP 可能随重启变化。当前推荐做法不是重装所有 agent，而是临时覆盖 `agent_api_url` 并批量热更新。

示例：平台地址变为 `172.21.10.13`

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://172.21.10.13:8000'
```

更新后立即批量检查：

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml'
```

如果要把新地址改成默认值，再修改：

- `ssh/group_vars/linux_hosts.yml` 中的 `agent_api_url`

## 8. 推荐执行流程

### 新增一台 Linux agent host

1. 先跑 `ansible ping`
2. 单机执行 `install_agent.yml`
3. 单机执行 `check_agent.yml`
4. 确认平台端能看到节点和设备

### 日常代码更新

1. 先选一台实验机执行 `update_agent.yml`
2. 用 `check_agent.yml` 确认结果
3. 再对全组或指定子集批量更新

### 开发环境平台 IP 变更

1. 确认新的 Windows 平台地址
2. 使用 `-e agent_api_url=http://<new-ip>:8000` 批量执行 `update_agent.yml`
3. 立即跑 `check_agent.yml`
4. 观察平台前端节点与设备归属是否恢复正常

## 9. 输出与判定规则

### `service_agent.yml`

- `status` 默认要求服务为 `active`
- 未安装主机会直接失败
- `start/restart` 后服务不是 `active` 会失败
- `stop` 后服务不是 `inactive` 会失败

### `check_agent.yml`

严格模式下，以下任一条件不满足都会失败：

- 安装目录存在
- `.env` 存在
- systemd 服务状态为 `active`
- `agentctl health` 返回码为 `0`

这意味着“未配置主机也 pass”的情况已被显式禁止。

## 10. 常见问题

### `ansible ping` 成功，但 `check_agent.yml` 失败

说明 SSH 可达，但 agent 未安装、`.env` 缺失、服务未启动，或 `agentctl health` 未通过。

### 服务在运行，但平台端没有节点

优先检查：

- 远端 `.env` 里的 `API_URL`
- 目标机到平台地址的网络连通性
- 后端 `/api/v1/heartbeat` 是否正常处理自动注册和心跳

### 开发环境 IP 改了，但 agent 还在连旧地址

当前正确做法不是重装，而是重新执行：

```bash
wsl bash -lc 'cd "$REPO_ROOT/ssh" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://<new-ip>:8000'
```

### 批量检查时未安装主机也通过

当前实现已经修正。若仍出现，需要确认：

- 使用的是当前 Phase A 分支中的 `check_agent.yml`
- `agent_check_strict` 没有被覆盖为 `false`

### `sudo` 失败

当前默认约定：

- `ansible_password` 同时作为 `ansible_become_password`

如果某台主机的 sudo 密码与登录密码不同，需要在 `inventory.ini` 中单独覆盖。

## 11. 已知限制

- 首次部署仍复用 `install_agent.sh`，不是完全原生 Ansible
- 热更新当前只自动回写 `API_URL`，不会模板化重建整份 `.env`
- 还没有实现 `serial` 滚动分批和失败主机汇总报告
- `inventory.ini` 为本地敏感文件，不纳入版本控制，需要各执行环境自行维护
- 当前文档面向开发/验证环境；生产环境建议使用固定平台地址或域名

## 12. 相关文件

- `ssh/README.md`
- `ssh/group_vars/linux_hosts.yml`
- `ssh/playbooks/install_agent.yml`
- `ssh/playbooks/update_agent.yml`
- `ssh/playbooks/service_agent.yml`
- `ssh/playbooks/check_agent.yml`
- `backend/agent/DEPLOY.md`
