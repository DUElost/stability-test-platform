"""#2205 守卫：agent 进程日志轮转**共享 task**（copytruncate）+ 两通道接线（#2218）。

背景：#2205 修复 = logrotate 配置经 Ansible 下发（48 台铺开后 94.5GB → 4.58GB）。
#2218 起 task 抽为共享文件 ``roles/agent_deploy/tasks/logrotate.yml``，由
``update_agent.yml``（linux_hosts，14 台部署面）与 ``configure_agents.yml``
（agent_config，48 台配置面）共同 include。本文件锁定：

1. 共享 task 配置两个日志文件 + **copytruncate**（fd 语义关键；独立 create token 禁）；
2. 路径经 ``agent_install_dir`` 变量渲染（不硬编码 /opt）；``become: true``；
   不含 ``delaycompress``（首轮即压缩）；
3. size/rotate 变量在 ``group_vars/all.yml``（跨面共享）且 task 内有 default 兜底；
4. 两 playbook 均 include 同一共享文件（单一维护点）；update 侧 include 带
   ``logrotate`` tag（支持 ``--tags logrotate`` 局部执行）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_UPDATE = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
PLAYBOOK_CONFIG = REPO_ROOT / "tools/ansible/playbooks/configure_agents.yml"
SHARED_TASK = REPO_ROOT / "tools/ansible/roles/agent_deploy/tasks/logrotate.yml"
GROUP_VARS_ALL = REPO_ROOT / "tools/ansible/group_vars/all.yml"

_TASK_INSTALL = "Ensure logrotate is installed (#2205)"
_TASK_CONFIGURE = "Configure agent log rotation (#2205)"


def _shared_tasks() -> list[dict]:
    return yaml.safe_load(SHARED_TASK.read_text(encoding="utf-8"))


def _find_task(tasks: list, name: str) -> dict:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(
        f"任务缺失：{name}（现有：{[t.get('name') for t in tasks]}）"
    )


def _content() -> str:
    return _find_task(_shared_tasks(), _TASK_CONFIGURE)["ansible.builtin.copy"]["content"]


def test_configures_both_log_files():
    """两个日志文件都要进轮转（agent.log 恒空但保留——stdout 一旦有输出同样受控）。"""
    content = _content()
    assert "{{ agent_install_dir }}/logs/agent_error.log" in content
    assert "{{ agent_install_dir }}/logs/agent.log" in content


def test_copytruncate_is_present_without_create():
    """fd 语义关键（#2205）：systemd `append:` 下 create 模式轮转静默失效。"""
    content = _content()
    assert "copytruncate" in content
    # 独立 token 级检查：copytruncate 自身含 "create" 子串，不能用子串判断
    assert not re.search(r"^\s*create\b", content, re.M), (
        "create 与 copytruncate 互斥——create 模式会让 systemd 继续写旧 fd"
    )


def test_paths_via_variable_and_root_owned():
    task = _find_task(_shared_tasks(), _TASK_CONFIGURE)
    copy = task["ansible.builtin.copy"]
    assert copy["dest"] == "/etc/logrotate.d/stp-agent"
    assert copy["owner"] == "root" and copy["group"] == "root"
    assert str(copy["mode"]) == "0644"
    assert task["become"] is True
    assert "/opt/stability-test-agent" not in copy["content"], "路径应经变量渲染"


def test_rotation_params_from_all_group_vars():
    """变量在 group_vars/all.yml（跨面共享，**不放 role defaults**——``--tags``
    会跳过 pre_tasks 的 include_vars 加载，2026-09-15 铺开实测 undefined）。"""
    content = _content()
    assert "size {{ agent_logrotate_size | default(" in content
    assert "rotate {{ agent_logrotate_rotate | default(" in content
    group_vars = yaml.safe_load(GROUP_VARS_ALL.read_text(encoding="utf-8"))
    assert group_vars["agent_logrotate_size"] == "200M"
    assert int(group_vars["agent_logrotate_rotate"]) >= 3


def test_rotation_policy_tokens():
    content = _content()
    for token in ("daily", "compress", "missingok", "notifempty"):
        assert token in content, f"轮转策略缺少 {token}"
    # #2205 补丁：去 delaycompress——首轮 .1 保持未压缩（GB 级常驻）会让
    # 「控总量」延迟一轮才兑现；本场景首轮即压缩。
    assert "delaycompress" not in content, (
        "delaycompress 会让首轮 .1 未压缩——本场景应首轮即压缩"
    )


def test_logrotate_package_installed_before_config():
    """主机未预装 logrotate（canary 实测 `logrotate: not found`）——
    安装 task 必须在配置 task 之前，且 apt 参数正确。"""
    tasks = _shared_tasks()
    names = [t.get("name") for t in tasks]
    assert names.index(_TASK_INSTALL) < names.index(_TASK_CONFIGURE)
    apt = _find_task(tasks, _TASK_INSTALL)["ansible.builtin.apt"]
    assert apt["name"] == "logrotate"
    assert apt["state"] == "present"


def _logrotate_includes(play: dict) -> list[dict]:
    return [
        t for t in (play.get("tasks") or [])
        if "agent_deploy/tasks/logrotate.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    ]


def test_shared_task_included_by_both_playbooks():
    """单一维护点（#2218）：两 playbook 都 include 同一共享文件。"""
    for path in (PLAYBOOK_UPDATE, PLAYBOOK_CONFIG):
        play = yaml.safe_load(path.read_text(encoding="utf-8"))[0]
        assert _logrotate_includes(play), f"{path.name} 未 include 共享 logrotate task"


def test_logrotate_tasks_carry_tag_explicitly():
    """tag 必须写在共享 task 的**各 task** 上（#2218 实测：include 处的 tags 在
    ansible-core 2.19 不传播到 included tasks——`--tags logrotate` 只跑 include
    本身、included tasks 被静默跳过；显式标注后才真正执行）。"""
    for task in _shared_tasks():
        assert "logrotate" in (task.get("tags") or []), (
            f"{task.get('name')} 缺显式 logrotate tag（不能依赖 include 传播）"
        )


def test_update_playbook_include_tagged_for_scoped_rollout():
    """update 面 include 亦带 logrotate tag（双保险：include 步骤本身可被 tag 选中）。"""
    play = yaml.safe_load(PLAYBOOK_UPDATE.read_text(encoding="utf-8"))[0]
    inc = _logrotate_includes(play)[0]
    assert "logrotate" in (inc.get("tags") or [])
