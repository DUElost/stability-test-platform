# Ansible Linux Agent Host Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `172.21.10.36` 上交付基于 Ansible 的 Linux agent host 首次部署、热更新、服务管理和状态检查闭环，并保留后续原生迁移到 role/tasks 的扩展点。

**Architecture:** 以 `ssh/` 作为唯一运维入口，新增 `group_vars`、`host_vars` 和四个 playbook。首次安装阶段先桥接现有 `install_agent.sh`，避免一次性重写高风险逻辑；热更新、服务管理和状态检查直接用 Ansible tasks 落地，减少对人工 SSH 的依赖。共享变量、排除列表和 handlers 放入 `ssh/roles/agent_deploy/`，为 Phase B 的原生迁移保留稳定边界。

**Tech Stack:** Ansible playbook、inventory/group_vars/host_vars、现有 `backend/agent/install_agent.sh` 与 `agentctl.sh`、systemd、WSL 命令行。

---

### Task 1: 建立共享变量和 Role 骨架

**Files:**
- Create: `ssh/group_vars/linux_hosts.yml`
- Create: `ssh/host_vars/172.21.10.36.yml`
- Create: `ssh/roles/agent_deploy/defaults/main.yml`
- Create: `ssh/roles/agent_deploy/handlers/main.yml`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-inventory -i inventory.ini --host 172.21.10.36'`

- [ ] **Step 1: 先验证当前 inventory 还没有部署变量**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-inventory -i inventory.ini --host 172.21.10.36'
```

Expected: 输出只有 `ansible_host`、`ansible_user`、`ansible_password` 等连接字段，不包含 `agent_api_url`、`agent_install_dir`、`agent_service_name`。

- [ ] **Step 2: 写入 Linux 主机默认变量**

Create `ssh/group_vars/linux_hosts.yml`:

```yaml
agent_api_url: "http://172.21.10.15:8000"
agent_install_dir: "/opt/stability-test-agent"
agent_service_name: "stability-test-agent"
agent_remote_tmp_dir: "/tmp/stability-test-agent-ansible"
agent_user: "android"
agent_group: "android"
agent_auto_register_host: true
agent_poll_interval: 10
agent_adb_path: "adb"
agent_log_level: "INFO"
agent_health_log_tail_lines: 40
agent_agentctl_path: "{{ agent_install_dir }}/agentctl"
agent_source_dir: "{{ playbook_dir }}/../../backend/agent"
```

- [ ] **Step 3: 为实验机写入单机覆盖变量**

Create `ssh/host_vars/172.21.10.36.yml`:

```yaml
agent_api_url: "http://172.21.10.15:8000"
```

- [ ] **Step 4: 写入 Role 默认值和共享 handlers**

Create `ssh/roles/agent_deploy/defaults/main.yml`:

```yaml
agent_allowed_service_actions:
  - start
  - stop
  - restart
  - status

agent_install_excludes:
  - "__pycache__/"
  - "test_agent*.py"
  - "test_aimonkey*.py"
  - "test_main*.py"
  - "tests/"
  - "install_agent.sh"
  - "sync_agent.sh"
  - "agentctl.sh"
  - "DEPLOY.md"
  - ".env.example"
  - "stability-test-agent.service"
  - "hosts.txt"
```

Create `ssh/roles/agent_deploy/handlers/main.yml`:

```yaml
- name: daemon reload
  become: true
  ansible.builtin.systemd:
    daemon_reload: true

- name: restart agent service
  become: true
  ansible.builtin.systemd:
    name: "{{ agent_service_name }}"
    state: restarted
```

- [ ] **Step 5: 重新查看 inventory，确认变量已生效**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-inventory -i inventory.ini --host 172.21.10.36'
```

Expected: 输出中出现 `agent_api_url`、`agent_install_dir`、`agent_service_name`、`agent_remote_tmp_dir` 等变量。

- [ ] **Step 6: 提交这一组基础设施改动**

Run:

```bash
git add ssh/group_vars/linux_hosts.yml ssh/host_vars/172.21.10.36.yml ssh/roles/agent_deploy/defaults/main.yml ssh/roles/agent_deploy/handlers/main.yml
git commit -m "chore(ansible): add shared linux agent vars"
```

Expected: 生成一条只包含变量与 handler 骨架的提交。

### Task 2: 实现服务管理 Playbook

**Files:**
- Create: `ssh/playbooks/service_agent.yml`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --syntax-check'`

- [ ] **Step 1: 先让 syntax-check 因文件不存在而失败**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --syntax-check'
```

Expected: FAIL，提示 `playbook: playbooks/service_agent.yml could not be found`。

- [ ] **Step 2: 写入服务管理 Playbook**

Create `ssh/playbooks/service_agent.yml`:

```yaml
- name: Manage linux agent service
  hosts: linux_hosts
  gather_facts: false
  vars:
    agent_service_action: "{{ agent_service_action | default('status') }}"

  pre_tasks:
    - name: Validate requested service action
      ansible.builtin.assert:
        that:
          - agent_service_action in agent_allowed_service_actions
        fail_msg: "agent_service_action must be one of {{ agent_allowed_service_actions }}"

  tasks:
    - name: Read service state before action
      become: true
      ansible.builtin.command: "systemctl is-active {{ agent_service_name }}"
      register: agent_service_state_before
      changed_when: false
      failed_when: false

    - name: Apply requested service action
      become: true
      ansible.builtin.systemd:
        name: "{{ agent_service_name }}"
        state: >-
          {{
            'started' if agent_service_action == 'start'
            else 'stopped' if agent_service_action == 'stop'
            else 'restarted'
          }}
        daemon_reload: "{{ agent_service_action == 'restart' }}"
      when: agent_service_action in ['start', 'stop', 'restart']

    - name: Read service state after action
      become: true
      ansible.builtin.command: "systemctl is-active {{ agent_service_name }}"
      register: agent_service_state_after
      changed_when: false
      failed_when: false

    - name: Print service action summary
      ansible.builtin.debug:
        msg:
          host: "{{ inventory_hostname }}"
          requested_action: "{{ agent_service_action }}"
          before: "{{ agent_service_state_before.stdout | default('unknown') }}"
          after: "{{ (agent_service_state_after.stdout if agent_service_action != 'status' else agent_service_state_before.stdout) | default('unknown') }}"
```

- [ ] **Step 3: 运行 syntax-check，确认语法通过**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --syntax-check'
```

Expected: PASS，不输出 YAML 解析错误。

- [ ] **Step 4: 对实验机执行 `status`，验证 playbook 可连通运行**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=status"
```

Expected: PASS，输出 `requested_action: status`，并返回当前 systemd 状态，即便服务尚未安装也不能出现语法或提权错误。

- [ ] **Step 5: 提交服务管理入口**

Run:

```bash
git add ssh/playbooks/service_agent.yml
git commit -m "feat(ansible): add agent service playbook"
```

Expected: 生成只包含 `service_agent.yml` 的提交。

### Task 3: 实现状态检查 Playbook

**Files:**
- Create: `ssh/playbooks/check_agent.yml`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --syntax-check'`

- [ ] **Step 1: 先让 syntax-check 因文件不存在而失败**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --syntax-check'
```

Expected: FAIL，提示 `playbook: playbooks/check_agent.yml could not be found`。

- [ ] **Step 2: 写入状态检查 Playbook**

Create `ssh/playbooks/check_agent.yml`:

```yaml
- name: Check linux agent host status
  hosts: linux_hosts
  gather_facts: false

  tasks:
    - name: Verify transport connectivity
      ansible.builtin.ping:

    - name: Check install directory
      become: true
      ansible.builtin.stat:
        path: "{{ agent_install_dir }}"
      register: agent_install_dir_stat

    - name: Check environment file
      become: true
      ansible.builtin.stat:
        path: "{{ agent_install_dir }}/.env"
      register: agent_env_stat

    - name: Check service active state
      become: true
      ansible.builtin.command: "systemctl is-active {{ agent_service_name }}"
      register: agent_service_active
      changed_when: false
      failed_when: false

    - name: Run agent health command when installed
      become: true
      ansible.builtin.command: "{{ agent_agentctl_path }} health"
      register: agent_health_result
      changed_when: false
      failed_when: false
      when: agent_install_dir_stat.stat.exists

    - name: Tail error log when present
      become: true
      ansible.builtin.shell: |
        tail -n {{ agent_health_log_tail_lines }} {{ agent_install_dir }}/logs/agent_error.log
      register: agent_error_log_tail
      changed_when: false
      failed_when: false
      when: agent_install_dir_stat.stat.exists

    - name: Print host summary
      ansible.builtin.debug:
        msg:
          host: "{{ inventory_hostname }}"
          install_dir_exists: "{{ agent_install_dir_stat.stat.exists }}"
          env_exists: "{{ agent_env_stat.stat.exists }}"
          service_state: "{{ agent_service_active.stdout | default('unknown') }}"
          health_rc: "{{ agent_health_result.rc | default('skipped') }}"
          health_stdout: "{{ agent_health_result.stdout_lines | default([]) }}"
          error_log_tail: "{{ agent_error_log_tail.stdout_lines | default([]) }}"
```

- [ ] **Step 3: 运行 syntax-check，确认语法通过**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --syntax-check'
```

Expected: PASS。

- [ ] **Step 4: 在实验机上执行检查，确认未安装和已安装两种场景都能返回可读结果**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```

Expected: PASS；如果尚未安装，应看到 `install_dir_exists: false`；安装后重跑时，应看到 `env_exists: true`、`service_state: active` 和 `health_stdout` 内容。

- [ ] **Step 5: 提交状态检查入口**

Run:

```bash
git add ssh/playbooks/check_agent.yml
git commit -m "feat(ansible): add agent health check playbook"
```

Expected: 生成只包含 `check_agent.yml` 的提交。

### Task 4: 实现首次部署 Playbook

**Files:**
- Create: `ssh/playbooks/install_agent.yml`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --syntax-check'`

- [ ] **Step 1: 先让 syntax-check 因文件不存在而失败**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --syntax-check'
```

Expected: FAIL，提示 `playbook: playbooks/install_agent.yml could not be found`。

- [ ] **Step 2: 写入首次部署 Playbook，桥接现有 `install_agent.sh`**

Create `ssh/playbooks/install_agent.yml`:

```yaml
- name: Install linux agent host via phase-a bridge
  hosts: linux_hosts
  gather_facts: false

  tasks:
    - name: Ensure remote temp directory exists
      become: true
      ansible.builtin.file:
        path: "{{ agent_remote_tmp_dir }}"
        state: directory
        mode: "0755"

    - name: Copy agent source tree to remote temp directory
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../../backend/agent/"
        dest: "{{ agent_remote_tmp_dir }}/"
        mode: preserve

    - name: Normalize line endings in staged source tree
      become: true
      ansible.builtin.shell: |
        find "{{ agent_remote_tmp_dir }}" -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i 's/\r$//' {} +
      changed_when: false

    - name: Run install script non-interactively
      become: true
      ansible.builtin.shell: |
        printf '%s\n\n' '{{ agent_api_url }}' | bash install_agent.sh
      args:
        chdir: "{{ agent_remote_tmp_dir }}"
      environment:
        AGENT_INSTALL_DIR: "{{ agent_install_dir }}"
        AGENT_USER: "{{ agent_user }}"
        AGENT_GROUP: "{{ agent_group }}"

    - name: Enable and start agent service
      become: true
      ansible.builtin.systemd:
        name: "{{ agent_service_name }}"
        enabled: true
        state: started
        daemon_reload: true

    - name: Verify service is active after install
      become: true
      ansible.builtin.command: "systemctl is-active {{ agent_service_name }}"
      register: agent_service_after_install
      changed_when: false

    - name: Print install summary
      ansible.builtin.debug:
        msg:
          host: "{{ inventory_hostname }}"
          service_state: "{{ agent_service_after_install.stdout }}"
          api_url: "{{ agent_api_url }}"
          install_dir: "{{ agent_install_dir }}"
```

- [ ] **Step 3: 运行 syntax-check，确认 playbook 可解析**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --syntax-check'
```

Expected: PASS。

- [ ] **Step 4: 在实验机上执行首次部署**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36"
```

Expected: PASS；`Run install script non-interactively` 任务完成，`Enable and start agent service` 成功，最后输出 `service_state: active`。

- [ ] **Step 5: 立即复用检查 Playbook 验证安装结果**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```

Expected: PASS；输出中 `install_dir_exists: true`、`env_exists: true`、`service_state: active`。

- [ ] **Step 6: 提交首次部署入口**

Run:

```bash
git add ssh/playbooks/install_agent.yml
git commit -m "feat(ansible): add phase-a install playbook"
```

Expected: 生成只包含 `install_agent.yml` 的提交。

### Task 5: 实现热更新 Playbook

**Files:**
- Create: `ssh/playbooks/update_agent.yml`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --syntax-check'`

- [ ] **Step 1: 先让 syntax-check 因文件不存在而失败**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --syntax-check'
```

Expected: FAIL，提示 `playbook: playbooks/update_agent.yml could not be found`。

- [ ] **Step 2: 写入热更新 Playbook，保留 `.env`、`venv`、`logs`**

Create `ssh/playbooks/update_agent.yml`:

```yaml
- name: Update linux agent host code
  hosts: linux_hosts
  gather_facts: false

  tasks:
    - name: Ensure remote temp directory exists
      become: true
      ansible.builtin.file:
        path: "{{ agent_remote_tmp_dir }}"
        state: directory
        mode: "0755"

    - name: Check install directory exists before update
      become: true
      ansible.builtin.stat:
        path: "{{ agent_install_dir }}"
      register: agent_install_dir_stat

    - name: Fail when target host is not installed
      ansible.builtin.assert:
        that:
          - agent_install_dir_stat.stat.exists
        fail_msg: "Agent is not installed on {{ inventory_hostname }}. Run install_agent.yml first."

    - name: Copy latest agent source tree to remote temp directory
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../../backend/agent/"
        dest: "{{ agent_remote_tmp_dir }}/"
        mode: preserve

    - name: Normalize line endings in staged source tree
      become: true
      ansible.builtin.shell: |
        find "{{ agent_remote_tmp_dir }}" -type f \( -name "*.sh" -o -name "*.py" \) -exec sed -i 's/\r$//' {} +
      changed_when: false

    - name: Sync Python code into installed agent directory
      become: true
      ansible.builtin.shell: |
        rsync -av --delete \
          --exclude='__pycache__/' \
          --exclude='test_agent*.py' \
          --exclude='test_aimonkey*.py' \
          --exclude='test_main*.py' \
          --exclude='tests/' \
          --exclude='agentctl.sh' \
          --exclude='install_agent.sh' \
          --exclude='sync_agent.sh' \
          --exclude='DEPLOY.md' \
          --exclude='.env.example' \
          --exclude='stability-test-agent.service' \
          --exclude='hosts.txt' \
          "{{ agent_remote_tmp_dir }}/" "{{ agent_install_dir }}/agent/"
      args:
        executable: /bin/bash

    - name: Refresh agentctl command
      become: true
      ansible.builtin.copy:
        src: "{{ playbook_dir }}/../../backend/agent/agentctl.sh"
        dest: "{{ agent_install_dir }}/agentctl"
        mode: "0755"

    - name: Refresh /usr/local/bin/agentctl symlink
      become: true
      ansible.builtin.file:
        src: "{{ agent_install_dir }}/agentctl"
        dest: "/usr/local/bin/agentctl"
        state: link

    - name: Repair ownership on install directory
      become: true
      ansible.builtin.file:
        path: "{{ agent_install_dir }}"
        owner: "{{ agent_user }}"
        group: "{{ agent_group }}"
        recurse: true

    - name: Reload systemd and restart service
      become: true
      ansible.builtin.systemd:
        name: "{{ agent_service_name }}"
        state: restarted
        daemon_reload: true

    - name: Tail error log after update
      become: true
      ansible.builtin.shell: |
        tail -n {{ agent_health_log_tail_lines }} {{ agent_install_dir }}/logs/agent_error.log
      register: agent_update_error_log
      changed_when: false
      failed_when: false

    - name: Print update summary
      ansible.builtin.debug:
        msg:
          host: "{{ inventory_hostname }}"
          error_log_tail: "{{ agent_update_error_log.stdout_lines | default([]) }}"
```

- [ ] **Step 3: 运行 syntax-check，确认 playbook 可解析**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --syntax-check'
```

Expected: PASS。

- [ ] **Step 4: 在实验机上执行热更新**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36"
```

Expected: PASS；`Sync Python code into installed agent directory` 与 `Reload systemd and restart service` 成功，不会重建 `.env` 和 `venv`。

- [ ] **Step 5: 再次运行检查和服务状态验证**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart"
```

Expected: 两条命令都 PASS；服务仍然 `active`，`agentctl health` 能返回正常输出。

- [ ] **Step 6: 提交热更新入口**

Run:

```bash
git add ssh/playbooks/update_agent.yml
git commit -m "feat(ansible): add agent update playbook"
```

Expected: 生成只包含 `update_agent.yml` 的提交。

### Task 6: 补全文档并完成端到端验收

**Files:**
- Create: `ssh/README.md`
- Modify: `backend/agent/DEPLOY.md`
- Test: `wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --syntax-check'`

- [ ] **Step 1: 先确认文档里还没有新的 Ansible 操作入口**

Run:

```bash
rg -n "playbooks/install_agent.yml|playbooks/update_agent.yml|playbooks/service_agent.yml|playbooks/check_agent.yml" ssh backend/agent/DEPLOY.md
```

Expected: 没有命中，或只有旧文档中不存在的结果。

- [ ] **Step 2: 新增 `ssh/README.md`，收敛 Ansible 运维命令**

Create `ssh/README.md`:

````md
# Linux Agent Host Ansible 运维

## 前提

- 在 WSL 中执行 Ansible
- 使用 `ssh/inventory.ini` 中的 `android` 账号连接目标主机
- 目标主机已配置免密 sudo

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

### 单机服务重启

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart"
```

### 单机状态检查

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```
````

- [ ] **Step 3: 在部署文档里增加 Ansible 入口说明**

Append to `backend/agent/DEPLOY.md` near the multi-host deployment section:

```md
## Ansible 运维入口

从 2026-04-20 起，Linux agent host 的推荐运维入口为 `ssh/` 下的 Ansible playbook。

- 首次部署：`ssh/playbooks/install_agent.yml`
- 热更新：`ssh/playbooks/update_agent.yml`
- 服务管理：`ssh/playbooks/service_agent.yml`
- 状态检查：`ssh/playbooks/check_agent.yml`

详细命令见 `ssh/README.md`。
```

- [ ] **Step 4: 统一执行 syntax-check，确认四个 playbook 都能解析**

Run:

```bash
wsl bash -lc 'cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --syntax-check && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --syntax-check'
```

Expected: 四条 syntax-check 全部 PASS。

- [ ] **Step 5: 完成实验机端到端验收**

Run:

```bash
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/install_agent.yml --limit 172.21.10.36"
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/service_agent.yml --limit 172.21.10.36 -e agent_service_action=restart"
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/update_agent.yml --limit 172.21.10.36"
wsl bash -lc "cd /mnt/f/stability-test-platform/ssh && ANSIBLE_CONFIG=./ansible.cfg ansible-playbook playbooks/check_agent.yml --limit 172.21.10.36"
```

Expected:
- 首次部署成功
- 服务管理可执行 `restart`
- 热更新不破坏 `.env`
- 最后一次 `check_agent.yml` 输出 `install_dir_exists: true`、`env_exists: true`、`service_state: active`

- [ ] **Step 6: 提交文档与验收结果**

Run:

```bash
git add ssh/README.md backend/agent/DEPLOY.md
git commit -m "docs(ansible): document linux agent playbook workflow"
```

Expected: 生成一条只包含文档更新的提交。

## Scope Note

本计划只覆盖 Phase A 单机闭环和 Phase B 的结构扩展点。完成 `172.21.10.36` 验收后，应基于真实运行结果再写一份新计划，将 `install_agent.sh` 的桥接安装迁移为原生 `role/tasks/template/systemd` 实现，并补充批量滚动执行策略。
