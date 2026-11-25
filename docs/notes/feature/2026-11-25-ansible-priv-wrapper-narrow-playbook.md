# 抽出共享 priv_wrapper Ansible 任务与独立窄 playbook

Status: implemented
Class: feature

- 日期：2026-11-25
- 关联：ADR-0037（#1250）、S4 wrapper 灰度铺开（`xhci-rebind`）

## Decision

1. 将 `tools/ansible/playbooks/update_agent.yml` 中的三步提权 wrapper 安装/自检任务（`Install agent privilege wrapper` → `Bootstrap privilege wrapper (conf + sudoers)` → `Verify privilege wrapper self-test`）抽为共享任务文件 `tools/ansible/roles/agent_deploy/tasks/priv_wrapper.yml`（每步显式标注 `tags: ["priv_wrapper"]`，匹配 ansible-core 2.19 不传播 `include_tasks` tags 的约束）。
2. 新增独立窄 playbook `tools/ansible/playbooks/update_priv_wrapper.yml`，与 `configure_journald.yml` 同形：
   - 默认目标 `hosts: "{{ priv_wrapper_hosts | default('agent_config') }}"`（覆盖 `linux_hosts` + `agent_legacy`，传入 `-e priv_wrapper_hosts=all` 可覆盖含 `agent_candidates` 的全队）；
   - `serial: "20%"`、`max_fail_percentage: 0`、`any_errors_fatal: true`；
   - `pre_tasks` 校验 `{{ agent_install_dir }}` 存在，防止误向未装机主机落盘 wrapper；
   - 严格范围隔离：不含代码 `rsync`、不获取 `upgrade-gate`、不重启 Agent 服务。
3. `update_agent.yml` 改为 `include_tasks` 复用 `priv_wrapper.yml`（单一维护点）。
4. 将 `stp_repo_root`、`agent_user`、`agent_group`、`agent_service_name` 上移至 `tools/ansible/group_vars/all.yml` 做跨面单一定义，移除 `group_vars/linux_hosts.yml` 中的重复定义（沿用 `#2218` 规范）。

## Alternatives

- **方案 A：仅给 `update_agent.yml` 的三步加 `--tags priv_wrapper`**
  - 缺点：`update_agent.yml` 目标写死 `hosts: linux_hosts`（15 台），无法覆盖 `agent_legacy`（34 台）与 `agent_candidates`（29 台）；且 `update_agent.yml` 的 `pre_tasks` 在带 tag 时仍需配合变量依赖处理，不如独立窄 playbook 直观且可复用。
- **方案 B：把 `priv_wrapper.yml` 直接并入 `configure_agents.yml`**
  - 缺点：`configure_agents.yml` 定位是常规主机系统配置面（logrotate / journald），把 root 提权二进制更新混入日常配置通道会扩大变更面，不符合最小权限与变更分离原则。
- **方案 C：修 `update_agent.yml` 的 `--rsync-path='sudo rsync'`**
  - 缺点：ADR-0037 已刻意收窄 `NOPASSWD: rsync`，且日常代码更新已全面走控制面热更新（`stp-agent-priv apply-code`）；为下发 wrapper 去改造已退居二线的 Ansible rsync 通道得不偿失。

## Verification

- 目标单测：`pytest tests/test_ansible_priv_wrapper_channel.py tests/test_agent_priv_boundary.py tests/test_update_agent_playbook.py tests/test_ansible_config_channel_2218.py tests/test_ansible_logrotate_2205.py tests/test_ansible_agent_secret_source.py tests/test_journald_baseline_3804.py`（含 `ansible-playbook --syntax-check` 验证 `update_priv_wrapper.yml` 与 `update_agent.yml`）。
- 仓库快速门禁：`npm run check:quick`（覆盖 ruff/compileall 等静态门禁）。

## Revisit

- 若后续全队 `agent_candidates` 正式并入 `agent_config` 或退役旧分组，可评估是否仍需 `priv_wrapper_hosts` 覆盖参数。
- 若未来 `update_agent.yml` 的旧 `sudo rsync` 代码同步段彻底退役（全面统一为热更新 + provisioning 窄 playbook），可同步清理 `update_agent.yml` 的冗余同步段。
