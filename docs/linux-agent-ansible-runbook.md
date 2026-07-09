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
- 日常运维入口统一收敛到 `tools/ansible/playbooks/*.yml`
- 后续 Phase B 再迁移到完全原生的 Ansible tasks

## 2. 目录与入口

当前实现位于主仓库：

- 仓库根目录：建议使用 Linux 本地路径，例如 `/opt/stability-test-platform` 或开发 checkout 路径
- Ansible 入口目录：`tools/ansible/`

关键文件：

- `tools/ansible/ansible.cfg`：Ansible 默认配置
- `tools/ansible/inventory.ini`：主机清单，本地敏感文件，不纳入版本控制
- `tools/ansible/group_vars/linux_hosts.yml`：Linux 主机默认变量
- `tools/ansible/playbooks/install_agent.yml`：首次部署
- `tools/ansible/playbooks/update_agent.yml`：热更新
- `tools/ansible/playbooks/service_agent.yml`：服务管理
- `tools/ansible/playbooks/check_agent.yml`：状态检查
- `tools/ansible/roles/agent_deploy/defaults/main.yml`：服务动作白名单等默认值

建议先定义仓库根路径：

```bash
export REPO_ROOT=/path/to/stability-test-platform
cd "$REPO_ROOT/tools/ansible"
```

## 3. 执行前提

- 优先在 Linux 控制主机或 Linux 运维环境中执行 Ansible
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

默认变量来源于 `tools/ansible/group_vars/linux_hosts.yml`。

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

生产与预发布默认约束：

- `HOST_ID` 应固定并与后端 `hosts.id` 对齐
- `AUTO_REGISTER_HOST=true` 仅保留给旧 agent / 临时实验兼容，不应作为批量运维默认值

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
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o
```

### 单机首次部署

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36
```

### 单机热更新

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36
```

### 单机状态检查

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36
```

### 单机重启服务

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart
```

### 单机查看服务状态

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=status
```

### 批量首次部署

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml
```

### 批量热更新

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml
```

### 批量服务重启

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml -e agent_service_action=restart
```

### 批量状态检查

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml
```

### 只操作部分主机

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit "172.21.10.36,172.21.15.7,172.21.15.2"
```

## 7. 开发环境 IP 变化时的标准流程

开发环境下平台 IP 可能变化。当前推荐做法不是重装所有 agent，而是临时覆盖 `agent_api_url` 并批量热更新。

示例：平台地址变为 `172.21.10.13`

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://172.21.10.13:8000
```

更新后立即批量检查：

```bash
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml
```

如果要把新地址改成默认值，再修改：

- `tools/ansible/group_vars/linux_hosts.yml` 中的 `agent_api_url`

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

1. 确认新的控制平面地址
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
cd "$REPO_ROOT/tools/ansible" && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml -e agent_api_url=http://<new-ip>:8000
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
- `inventory.ini` 为本地敏感文件，不纳入版本控制，需要各执行环境自行维护
- 当前文档默认以 Linux-first 运维为准；Windows / WSL 仅作为兼容入口保留

## 11a. UI 触发的 Agent 安装与热更新

除 CLI（`ansible-playbook`）外，控制平面提供 UI 入口，适合开发期高频同步代码到子 agent。

### 前提

- 控制平面已装 `ansible-core` + `sshpass`（首次安装端点会自动 `shutil.which` 检测，缺失返回 501 + 提示装什么）
- 可选：`STP_AGENT_PIP_INDEX_URL=<镜像>`（agent 主机 pip 镜像；留空用公网 PyPI）
- 可选：`STP_SSH_KNOWN_HOSTS=<路径>`（host key 信任文件；默认 `~/.ssh/known_hosts`）

### 新增主机（自动 ssh-keyscan）

1. UI「主机管理」→「添加主机」，填 IP/SSH 端口/SSH 用户/密码
2. 后端 `create_host` 落库后自动 `ssh-keyscan` 写入 known_hosts
3. 成功：响应 `host_key_trust="ok"`；失败：`host_key_trust="failed: <reason>"` + 前端 info 提示，**不阻塞建主机**
4. 失败时需手动 `ssh-keyscan -p <port> <ip> >> <known_hosts>` 后再热更新/安装

### 首次安装（UI 按钮）

1. 主机行 `status != ONLINE` 时显示「首次安装」按钮（ONLINE 显示「热更新」）
2. 点击 → `POST /api/v1/hosts/{id}/install` → SAQ 异步任务 `install_agent_task`
3. 任务写临时 inventory（`ansible_become_password` = SSH 密码），调 `install_agent.yml --limit <ip>`
4. ansible stdout 落盘 `$STP_INSTALL_LOG_DIR/install_<host_id>_<ts>.log`（默认 `/tmp/stp-install-logs/`）
5. `install_agent.sh:79-113` 自动写 `/etc/sudoers.d/stability-test-agent` NOPASSWD（rsync/systemctl），**装完即解锁后续免密热更新**
6. 前端每 3s 轮询 `GET /hosts/{id}/install/status`，终态 toast 通知

### 热更新（UI 按钮，高频）

1. ONLINE 主机行显示「热更新」按钮 → `POST /api/v1/hosts/{id}/hot-update`
2. 后端 `execute_hot_update`：tar 打包 `backend/agent/` → SFTP → 远端 rsync 到 `/opt/stability-test-agent/agent/`
3. **依赖刷新**：rsync 前后比对 `requirements.txt` sha256，变化则 `pip install -r`（`PIP_INDEX_URL` 取 `STP_AGENT_PIP_INDEX_URL`）；pip 失败则**不重启**服务以避免崩溃
4. **版本审计**：响应含 `code_version`（`backend/agent` 的 `git rev-parse --short HEAD`）与 `deps_refreshed`，写入 `audit_log`（`hot_update` + `hot_update_result` 两条）
5. 前端成功 toast 显示「(依赖已刷新/未变) @<code_version>」
6. sudo 免密靠首次安装落的 sudoers.d；未装主机点热更新会因 `INSTALL_DIR` 缺失失败 → 先用「首次安装」

### pip 镜像不可达时的兜底

- 在线：`STP_AGENT_PIP_INDEX_URL=<内网/公网镜像>`
- 离线 wheelhouse（首次安装）：预置 `STP_AGENT_WHEELHOUSE=<wheel 目录>`（计划项，当前 install playbook 仅注入 `PIP_INDEX_URL`；离线 `--no-index --find-links` 兜底为后续增强）
- 两者皆无：install/热更新会在 pip 步骤失败并给出明确错误，不会静默继续

### 相关环境变量

| 变量 | 作用 | 默认 |
|------|------|------|
| `STP_AGENT_PIP_INDEX_URL` | agent pip 镜像（热更新 + 首次安装） | 空（公网 PyPI） |
| `STP_SSH_KNOWN_HOSTS` | host key 信任文件 | `~/.ssh/known_hosts` |
| `STP_INSTALL_LOG_DIR` | UI 安装 ansible 日志目录 | `/tmp/stp-install-logs` |

> 首次安装端点（`POST /hosts/{id}/install`）不依赖 env 开关，请求时自动检测 `ansible-playbook` + `sshpass` 是否在 PATH，缺失返回 501。

## 12. 相关文件

- `tools/ansible/README.md`
- `tools/ansible/group_vars/linux_hosts.yml`
- `tools/ansible/playbooks/install_agent.yml`
- `tools/ansible/playbooks/update_agent.yml`
- `tools/ansible/playbooks/service_agent.yml`
- `tools/ansible/playbooks/check_agent.yml`
- `backend/agent/DEPLOY.md`
