"""#2218 守卫：配置面通道（agent_config）与更新面（linux_hosts）分离。

背景：34 台 legacy 主机不在原 Ansible 正式面，无「主机配置类变更」下发通道
（#2205 logrotate 铺开以 ad-hoc 绕过暴露）。裁决 = 新建 `agent_config` 组（48 台）
+ `configure_agents.yml`（**只做配置类变更**），与 `update_agent.yml`（agent 版本
更新面，14 台）分离；34 台 agent 代码更新仍走热更新。本文件锁定：

1. `configure_agents.yml` 存在且 `hosts: agent_config`（fail-fast 与 update 面同款）；
2. **范围隔离**：其 task 面不得含 agent 更新类操作（rsync/restart/hot-update 等，
   注释除外——注释允许说明边界）；
3. 共享 logrotate task 被配置面 include（与 update 面单一维护点）；
4. `group_vars/all.yml` 提供跨面变量（become/install_dir/logrotate），且
   `linux_hosts.yml` 不重复定义（单一定义防漂移）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_CONFIG = REPO_ROOT / "tools/ansible/playbooks/configure_agents.yml"
GROUP_VARS_ALL = REPO_ROOT / "tools/ansible/group_vars/all.yml"
GROUP_VARS_LINUX = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"

# 更新类操作的禁词（配置面不得出现；范围隔离的机械锚）
_UPDATE_VERBS = (
    "rsync",
    "synchronize",
    "systemctl restart",
    "hot_update",
    "hot-update",
    "install_agent.sh",
    "update_agent.yml",
)


def _config_play() -> dict:
    return yaml.safe_load(PLAYBOOK_CONFIG.read_text(encoding="utf-8"))[0]


def test_config_playbook_targets_agent_config_group():
    play = _config_play()
    assert play["hosts"] == "agent_config"
    assert play.get("any_errors_fatal") is True
    assert play.get("max_fail_percentage") == 0


def test_config_playbook_excludes_update_verbs():
    """范围隔离：配置面不得做 agent 版本更新类操作（#2218 裁决）。"""
    code = "\n".join(
        line for line in PLAYBOOK_CONFIG.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )
    for verb in _UPDATE_VERBS:
        assert verb not in code, f"配置面出现更新类操作：{verb}"


def test_config_playbook_includes_shared_logrotate():
    play = _config_play()
    includes = [
        t for t in play.get("tasks", [])
        if "agent_deploy/tasks/logrotate.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    ]
    assert includes, "配置面未 include 共享 logrotate task"


def test_shared_vars_available_to_both_planes():
    """group_vars/all.yml 对两面生效；linux_hosts.yml 不重复定义（单一定义）。"""
    gv = yaml.safe_load(GROUP_VARS_ALL.read_text(encoding="utf-8"))
    for key in (
        "ansible_become", "ansible_become_method", "ansible_become_password",
        "agent_install_dir", "agent_logrotate_size", "agent_logrotate_rotate",
    ):
        assert key in gv, f"all.yml 缺少跨面共享变量 {key}"
    linux = yaml.safe_load(GROUP_VARS_LINUX.read_text(encoding="utf-8"))
    for key in ("agent_install_dir", "agent_logrotate_size", "agent_logrotate_rotate"):
        assert key not in linux, f"linux_hosts.yml 仍重复定义 {key}（应只在 all.yml）"
