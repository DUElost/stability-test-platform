# Ansible 替代 Linux Agent Host 自编写脚本运维设计

**日期**: 2026-04-20

## 背景

当前 Linux agent host 的运维方式以 `backend/agent/install_agent.sh` 和 `backend/agent/sync_agent.sh` 为主，首次部署、热更新、批量重启、状态检查主要依赖人工执行脚本、`ssh` 和 `systemctl`。仓库中虽然已有 `ssh/ansible.cfg`、`ssh/inventory.ini` 和 `ssh/ssh_key.yml`，但目前仅覆盖基础连通性与 SSH 公钥分发，没有形成完整的批量部署与运维闭环。

本次目标是引入 Ansible 作为 Linux agent host 的统一运维入口，替代“手工脚本 + 手工登录目标机”的模式，并以 `172.21.10.36` 作为单机实验对象，逐步扩展到 `linux_hosts`。

## 目标

- 使用 Ansible 统一接管 Linux agent host 的首次部署、热更新、批量重启和状态检查。
- 保持现有 `inventory.ini` 的主机清单模型，连接用户继续使用 `android`，通过免密 `sudo/become` 提权执行安装、同步和服务管理。
- Phase A 先在 `172.21.10.36` 跑通完整闭环。
- Phase B 再把当前脚本运维逐步原生迁移到 Ansible roles/tasks。
- 允许统一默认配置，同时允许个别主机通过 `host_vars` 覆盖差异参数。

## 非目标

- 本轮不改动后端 Agent 运行逻辑，不修改 `backend/agent/main.py` 的业务行为。
- 本轮不改变 `inventory.ini` 中主机登录账号模型，不切换为 `root` 直连。
- 本轮不在 Phase A 完全删除 `install_agent.sh` 与 `sync_agent.sh`，它们在过渡期仍作为回退和能力参考。

## 现状与约束

### 当前部署链路

- 首次安装入口为 `backend/agent/install_agent.sh`。
- 热更新入口为 `backend/agent/sync_agent.sh`。
- 已有部署说明位于 `backend/agent/DEPLOY.md`。
- Agent 安装目录默认是 `/opt/stability-test-agent`。
- 服务名固定为 `stability-test-agent`。
- `.env` 中 `HOST_ID=auto`、`AUTO_REGISTER_HOST=true` 已是当前推荐模式。

### 当前 Ansible 基础

- `ssh/ansible.cfg` 已设置默认 inventory 为 `./inventory.ini`，并关闭 host key checking。
- `ssh/inventory.ini` 已维护 `linux_hosts` 分组，并包含实验机 `172.21.10.36`。
- 已验证以下命令可正常执行，说明 Ansible 到目标主机的基础连通性成立：

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible -i inventory.ini linux_hosts -m ping -o | grep SUCCESS'
```

### 已确认的设计约束

- 远程连接用户继续使用 `android`。
- 所有需要安装、同步、`systemctl` 的动作通过免密 `sudo/become` 执行。
- 配置模型采用“统一默认值 + 个别主机覆盖”。
- Phase A 的 `.env` 生成仍沿用安装脚本逻辑，只是把交互输入改成可由 Ansible 注入的非交互参数。

## 总体方案

采用“两阶段替代 + 一阶段扩容”的迁移路径。

### Phase A：单机跑通 Ansible 运维闭环

目标主机为 `172.21.10.36`，范围包括：

- 首次部署
- 热更新
- 服务重启与状态管理
- 健康检查与诊断输出

该阶段允许复用现有 `install_agent.sh` 和 `sync_agent.sh` 的核心能力，但禁止继续依赖人工 SSH 登录和手工输入。所有运维动作必须通过 `ssh/` 下的 playbook 触发，且支持 `--limit 172.21.10.36`。

### Phase B：原生迁移到 Ansible

在 Phase A 验证通过后，逐步将以下能力从 shell 脚本迁移为原生 Ansible 任务：

- 安装目录创建
- 代码同步
- `.env` 模板生成
- systemd service 安装与更新
- 依赖安装
- 重启与校验

迁移完成后，`install_agent.sh` 与 `sync_agent.sh` 不再作为主入口，仅保留短期回退价值。

### Phase C：批量化扩展

当 `172.21.10.36` 试点稳定后，扩展到 `linux_hosts`：

- 批量部署
- 批量热更新
- 批量重启
- 批量健康检查
- 支持 `--limit` 指定子集主机
- 支持失败隔离和滚动执行

## 建议目录结构

Ansible 运维入口统一收敛到 `ssh/`，目录结构如下：

```text
ssh/
├── ansible.cfg
├── inventory.ini
├── group_vars/
│   └── linux_hosts.yml
├── host_vars/
│   └── 172.21.10.36.yml
├── playbooks/
│   ├── install_agent.yml
│   ├── update_agent.yml
│   ├── service_agent.yml
│   └── check_agent.yml
├── roles/
│   └── agent_deploy/
│       ├── defaults/
│       ├── handlers/
│       ├── tasks/
│       ├── templates/
│       └── files/
└── templates/
    └── agent.env.j2
```

说明：

- `playbooks/` 是统一入口，Phase A 和 Phase B 都沿用同样的调用方式。
- `group_vars/` 负责 Linux agent host 的默认变量。
- `host_vars/` 只存放差异化主机配置。
- `roles/agent_deploy/` 在 Phase B 承接原生安装与更新逻辑。
- `templates/agent.env.j2` 在 Phase B 用于取代脚本交互式 `.env` 生成。

## 变量模型

### inventory.ini

仅保留连接层信息：

- `ansible_host`
- `ansible_user`
- `ansible_password`
- `ansible_port`（如后续需要）

不在 `inventory.ini` 中堆放部署逻辑变量，避免连接信息与业务配置耦合。

### group_vars/linux_hosts.yml

存放默认部署参数，例如：

- `agent_api_url`
- `agent_install_dir`
- `agent_service_name`
- `agent_remote_tmp_dir`
- `agent_user`
- `agent_group`
- `agent_auto_register_host`
- `agent_poll_interval`
- `agent_adb_path`
- `agent_log_level`
- `agent_health_log_tail_lines`

### host_vars/172.21.10.36.yml

存放实验机差异项，例如：

- 单独的 `agent_api_url`
- `ANDROID_ADB_SERVER_PORT`
- `EXTERNAL_TOOL_DIR`
- 试点期专用的临时目录或特殊校验参数

## Playbook 设计

### install_agent.yml

用途：首次部署。

Phase A 的实现策略：

1. 将 `backend/agent/` 同步到远端临时目录。
2. 修正脚本 CRLF。
3. 以非交互方式执行 `install_agent.sh`。
4. 向脚本注入 `API_URL` 等必要参数，避免人工输入。
5. 安装后执行 `systemctl daemon-reload`、`systemctl start stability-test-agent`。
6. 校验服务状态与关键目录。

设计原则：

- 复用现有脚本，降低首轮迁移风险。
- 保持可重复执行，不要求人工登录目标机。
- 安装失败时不做破坏性回滚，保留现场用于诊断。

### update_agent.yml

用途：热更新。

Phase A 的实现策略：

1. 将最新 `backend/agent/` 同步到远端临时目录。
2. 仅覆盖代码与管理脚本。
3. 保留目标机现有 `.env`、`logs/`、`venv/`、本地状态文件。
4. 执行 `systemctl daemon-reload`。
5. 重启 `stability-test-agent`。
6. 输出重启后的状态与关键日志尾部。

设计原则：

- 不做整机重装。
- 不重建 venv 和日志目录。
- 重启失败时立即输出诊断信息。

### service_agent.yml

用途：统一服务管理。

支持动作：

- `start`
- `stop`
- `restart`
- `status`

调用方式通过变量 `agent_service_action` 指定，例如：

```bash
ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart
```

设计原则：

- 批量执行时单台失败不阻塞其他主机。
- 结果中必须明确列出失败主机。

### check_agent.yml

用途：状态检查与诊断。

检查项包括：

- Ansible 连接是否成功
- `become` 是否成功
- `systemctl is-active stability-test-agent`
- `agentctl health`
- `/opt/stability-test-agent/.env` 是否存在
- 安装目录与关键日志文件是否存在
- 最近若干行 `journalctl` 或 `agent_error.log`

设计原则：

- 按“连接层、权限层、部署层、运行层”分层输出结果。
- 失败时直接暴露诊断信息，减少二次 SSH 排查。

## 执行规范

### 单机首次部署

```bash
cd /mnt/f/stability-test-platform/ssh
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36
```

### 单机热更新

```bash
cd /mnt/f/stability-test-platform/ssh
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36
```

### 单机服务重启

```bash
cd /mnt/f/stability-test-platform/ssh
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart
```

### 单机状态检查

```bash
cd /mnt/f/stability-test-platform/ssh
ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36
```

后续批量执行时，仅需将 `--limit 172.21.10.36` 替换为目标主机组或主机子集。

## 失败处理与回退策略

### 首次部署失败

- 不主动卸载已创建目录、用户、venv 或 service 文件。
- 保留远端临时目录与安装日志。
- 通过 Ansible 直接输出 `systemctl status` 和错误日志尾部。
- 后续修复后允许重复执行同一 playbook。

原因是首次安装涉及目录、权限、依赖和 systemd，误回滚比保留现场更危险。

### 热更新失败

- 如果同步失败，不进入覆盖和重启阶段。
- 如果重启失败，保留已同步文件。
- 立即输出以下诊断信息：
  - `systemctl status stability-test-agent`
  - `journalctl -u stability-test-agent` 尾部
  - `/opt/stability-test-agent/logs/agent_error.log` 尾部

### 服务管理失败

- 不中断其他主机执行。
- 必须在总结结果中点名失败主机。

### 检查失败

- 必须区分 SSH 失败、sudo 失败、服务失败、健康检查失败四类。
- 避免只返回“失败”而没有定位层级。

## 验收标准

### Phase A 验收

以 `172.21.10.36` 为实验机，以下项目全部通过：

- `install_agent.yml` 可以完成首次部署。
- `update_agent.yml` 可以完成热更新，且不覆盖 `.env`。
- `service_agent.yml` 可以完成 `restart` 与 `status`。
- `check_agent.yml` 可以完成健康检查并返回可读诊断结果。
- 整个流程不需要手工 SSH 到目标机执行运维命令。

### Phase B 验收

- 首次部署不再依赖 `install_agent.sh`。
- 热更新不再依赖 `sync_agent.sh`。
- `.env` 改由模板化生成。
- 原生 Ansible 任务仍支持单机 `--limit 172.21.10.36` 验证。
- 单机稳定后再扩展到 `linux_hosts` 批量执行。

## 风险与控制点

- `install_agent.sh` 当前是交互式脚本，Phase A 需要明确其非交互执行输入方式，否则自动化会卡住。
- `inventory.ini` 当前直接保存主机密码，后续如果需要提高安全性，应迁移到 Ansible Vault 或独立变量文件。
- 批量执行时如果全部同时重启，可能造成所有 agent 短时离线，因此 Phase C 需要增加滚动策略。
- 现有脚本和 Ansible 在过渡期会双轨存在，必须保证 Ansible 是唯一推荐入口，避免两套流程长期漂移。

## 结论

本方案采用“先单机跑通，再原生迁移”的路径，以最小风险完成 Linux agent host 从自编写脚本运维到 Ansible 运维的替代。Phase A 先在 `172.21.10.36` 上建立首次部署、热更新、服务管理和状态检查的闭环；Phase B 再逐步将安装和同步逻辑迁移为原生 Ansible 任务；Phase C 扩展到 `linux_hosts` 批量执行。该路径兼顾现网稳定性、迁移可诊断性和后续批量运维能力。
