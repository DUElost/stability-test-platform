"""#2205 守卫：update_agent.yml 必须下发 agent 进程日志轮转（copytruncate）。

背景：agent 进程日志（systemd `StandardError=append:`）此前无任何轮转——
2026-09-15 全队实测 48/48 台合计 94.5GB、最大单台 4.89GB、≈54MB/天/台。
修复 = 经 agent_deploy 链下发 `/etc/logrotate.d/stp-agent`。本文件锁定：

1. task 存在且配置两个日志文件（agent_error.log / agent.log）；
2. **copytruncate 必须在**（systemd `append:` 持 fd，create 模式会让轮转
   静默失效——systemd 继续写已改名的旧文件）；
3. 路径经 ``agent_install_dir`` 变量渲染（不硬编码 /opt）；
4. size/rotate 变量在 ``group_vars``（**不放 role defaults**——``--tags`` 会跳过
   pre_tasks 的 include_vars 加载，2026-09-15 铺开实测 undefined）且 task 内
   有 ``| default()`` 兜底；
5. ``become: true``（写 /etc/logrotate.d 需 root）；
6. 不含 ``delaycompress``（补丁：首轮即压缩，控总量优先）。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
GROUP_VARS = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"

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


def test_rotation_params_from_group_vars():
    """变量在 group_vars（恒加载）+ task 内 default 兜底（#2205 补丁）。

    原放 role defaults——`--tags logrotate` 局部执行跳过 pre_tasks 的
    include_vars 加载，2026-09-15 铺开首跑 `undefined` 失败。
    """
    content = _content()
    assert "size {{ agent_logrotate_size | default(" in content
    assert "rotate {{ agent_logrotate_rotate | default(" in content
    group_vars = yaml.safe_load(GROUP_VARS.read_text(encoding="utf-8"))
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
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    names = [t.get("name") for t in play["tasks"]]
    install_name = "Ensure logrotate is installed (#2205)"
    assert install_name in names, f"安装任务缺失（现有：{names}）"
    assert names.index(install_name) < names.index(_TASK_NAME)
    apt = _find_task(play["tasks"], install_name)["ansible.builtin.apt"]
    assert apt["name"] == "logrotate"
    assert apt["state"] == "present"


def test_tasks_tagged_for_scoped_rollout():
    """两 task 带 `logrotate` tag——支持 `--tags logrotate` 只铺配置（免全量更新）。"""
    play = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))[0]
    for name in ("Ensure logrotate is installed (#2205)", _TASK_NAME):
        task = _find_task(play["tasks"], name)
        assert "logrotate" in (task.get("tags") or []), f"{name} 缺 logrotate tag"
