"""#2205 守卫：update_agent.yml 必须下发 agent 进程日志轮转（copytruncate）。

背景：agent 进程日志（systemd `StandardError=append:`）此前无任何轮转——
2026-09-15 全队实测 48/48 台合计 94.5GB、最大单台 4.89GB、≈54MB/天/台。
修复 = 经 agent_deploy 链下发 `/etc/logrotate.d/stp-agent`。本文件锁定：

1. task 存在且配置两个日志文件（agent_error.log / agent.log）；
2. **copytruncate 必须在**（systemd `append:` 持 fd，create 模式会让轮转
   静默失效——systemd 继续写已改名的旧文件）；
3. 路径经 ``agent_install_dir`` 变量渲染（不硬编码 /opt）；
4. size/rotate 由 defaults 变量控制且取值合理；
5. ``become: true``（写 /etc/logrotate.d 需 root）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
DEFAULTS = REPO_ROOT / "tools/ansible/roles/agent_deploy/defaults/main.yml"

_TASK_NAME = "Configure agent log rotation (#2205)"


def _find_task(tasks: list, name: str) -> dict:
    for task in tasks:
        if task.get("name") == name:
            return task
    raise AssertionError(
        f"任务缺失：{name}（现有：{[t.get('name') for t in tasks]}）"
    )


def _content() -> str:
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    task = _find_task(play["tasks"], _TASK_NAME)
    return task["ansible.builtin.copy"]["content"]


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
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    task = _find_task(play["tasks"], _TASK_NAME)
    copy = task["ansible.builtin.copy"]
    assert copy["dest"] == "/etc/logrotate.d/stp-agent"
    assert copy["owner"] == "root" and copy["group"] == "root"
    assert str(copy["mode"]) == "0644"
    assert task["become"] is True
    assert "/opt/stability-test-agent" not in copy["content"], "路径应经变量渲染"


def test_rotation_params_from_defaults():
    content = _content()
    assert "size {{ agent_logrotate_size }}" in content
    assert "rotate {{ agent_logrotate_rotate }}" in content
    defaults = yaml.safe_load(DEFAULTS.read_text(encoding="utf-8"))
    assert defaults["agent_logrotate_size"] == "200M"
    assert int(defaults["agent_logrotate_rotate"]) >= 3


def test_rotation_policy_tokens():
    content = _content()
    for token in ("daily", "compress", "delaycompress", "missingok", "notifempty"):
        assert token in content, f"轮转策略缺少 {token}"


def test_logrotate_package_installed_before_config():
    """主机未预装 logrotate（canary 实测 `logrotate: not found`）——
    安装 task 必须在配置 task 之前，且 apt 参数正确。"""
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    names = [t.get("name") for t in play["tasks"]]
    install_name = "Ensure logrotate is installed (#2205)"
    assert install_name in names, f"安装任务缺失（现有：{names}）"
    assert names.index(install_name) < names.index(_TASK_NAME)
    apt = _find_task(play["tasks"], install_name)["ansible.builtin.apt"]
    assert apt["name"] == "logrotate"
    assert apt["state"] == "present"
