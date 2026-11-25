"""ADR-0037 窄通道守卫：共享 priv_wrapper task 与 update_priv_wrapper.yml。

背景：2026-11-25 生产 canary 实跑 update_agent.yml 下发新版
stp-agent-priv（含 xhci-rebind）时发现两点运维现实：
1. ADR-0037 bootstrap 已把 Agent 用户 NOPASSWD sudoers 收窄为仅允许 wrapper +
   固定 systemctl，移除 NOPASSWD rsync；update_agent.yml 后段旧 `--rsync-path='sudo rsync'`
   在已收窄主机上因需交互输入密码报错退出（rc=12）；
2. update_agent.yml 仅覆盖 linux_hosts（15 台），无法覆盖 agent_legacy（34 台）与
   agent_candidates（29 台）。

本文件锁定：
1. 共享 task `roles/agent_deploy/tasks/priv_wrapper.yml` 按序执行 Install → bootstrap → selftest，
   路径/属主/权限合规，每步显式带 `priv_wrapper` tag（ansible-core 2.19 不向 include 传播）；
2. `update_agent.yml` 与 `update_priv_wrapper.yml` 均 include 同一共享文件（单一维护点）；
3. `update_priv_wrapper.yml` 默认目标为 `agent_config`（支持 `priv_wrapper_hosts` 覆盖）、
   fail-fast 语义（`any_errors_fatal: true` + `max_fail_percentage: 0`）、前置校验安装目录存在，
   且严格范围隔离：不含 rsync / 服务重启 / upgrade-gate 等更新动词；
4. 跨面变量 `stp_repo_root` / `agent_user` / `agent_group` / `agent_service_name`
   统一定义在 `group_vars/all.yml`，不在 `linux_hosts.yml` 重复定义。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAYBOOK_UPDATE = REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"
PLAYBOOK_PRIV = REPO_ROOT / "tools/ansible/playbooks/update_priv_wrapper.yml"
SHARED_TASK = REPO_ROOT / "tools/ansible/roles/agent_deploy/tasks/priv_wrapper.yml"
GROUP_VARS_ALL = REPO_ROOT / "tools/ansible/group_vars/all.yml"
GROUP_VARS_LINUX = REPO_ROOT / "tools/ansible/group_vars/linux_hosts.yml"

_PRIV_PLAYBOOK_REL = "tools/ansible/playbooks/update_priv_wrapper.yml"
_SHARED_TASK_REL = "tools/ansible/roles/agent_deploy/tasks/priv_wrapper.yml"
#: 真源锚点：窄 playbook / 共享 task 仍承载「只装 wrapper」语义（#2639）。
_PRIV_PLAY_ANCHOR = "Update agent privilege wrapper (ADR-0037)"
_SHARED_TASK_ANCHOR = "Install agent privilege wrapper"

_FORBIDDEN_UPDATE_VERBS = (
    "rsync",
    "synchronize",
    "systemctl restart",
    "state: restarted",
    "upgrade-gate",
    "hot_update",
    "hot-update",
    "install_agent.sh",
    "update_agent.yml",
)

_SHARED_VAR_KEYS = (
    "agent_install_dir",
    "agent_service_name",
    "agent_user",
    "agent_group",
    "stp_repo_root",
)


def _priv_play() -> dict:
    return yaml.safe_load(PLAYBOOK_PRIV.read_text(encoding="utf-8"))[0]


def _shared_tasks() -> list[dict]:
    return yaml.safe_load(SHARED_TASK.read_text(encoding="utf-8"))


def _priv_includes(play: dict) -> list[dict]:
    return [
        t
        for t in (play.get("tasks") or [])
        if "agent_deploy/tasks/priv_wrapper.yml" in str(t.get("ansible.builtin.include_tasks", ""))
    ]


def test_shared_priv_wrapper_task_order_and_permissions():
    tasks = _shared_tasks()
    names = [t.get("name") for t in tasks]
    assert names == [
        "Install agent privilege wrapper",
        "Bootstrap privilege wrapper (conf + sudoers)",
        "Verify privilege wrapper self-test",
    ]

    install_task, bootstrap_task, selftest_task = tasks
    copy_args = install_task["ansible.builtin.copy"]
    assert copy_args["src"] == "{{ stp_repo_root }}/backend/agent/stp_agent_priv.py"
    assert copy_args["dest"] == "/usr/local/sbin/stp-agent-priv"
    assert copy_args["owner"] == "root"
    assert copy_args["group"] == "root"
    assert str(copy_args["mode"]) == "0755"

    bootstrap_cmd = str(bootstrap_task["ansible.builtin.command"])
    assert "/usr/local/sbin/stp-agent-priv bootstrap" in bootstrap_cmd
    assert "--install-dir {{ agent_install_dir }}" in bootstrap_cmd
    assert "--user {{ agent_user }}" in bootstrap_cmd
    assert "--group {{ agent_group }}" in bootstrap_cmd
    assert "--service {{ agent_service_name }}" in bootstrap_cmd

    assert str(selftest_task["ansible.builtin.command"]).strip() == "/usr/local/sbin/stp-agent-priv selftest"

    for task in tasks:
        assert task.get("become") is True
        assert "priv_wrapper" in (task.get("tags") or []), (
            f"{task.get('name')} 缺显式 priv_wrapper tag（ansible-core 2.19 不传播 include tags）"
        )


def test_both_playbooks_include_shared_priv_wrapper_tasks():
    for path in (PLAYBOOK_UPDATE, PLAYBOOK_PRIV):
        play = yaml.safe_load(path.read_text(encoding="utf-8"))[0]
        includes = _priv_includes(play)
        assert len(includes) == 1, f"{path.name} 必须恰好 include 一次 priv_wrapper.yml"
        assert "priv_wrapper" in (includes[0].get("tags") or [])


def test_narrow_playbook_targeting_and_fail_fast_semantics():
    play = _priv_play()
    assert play["hosts"] == "{{ priv_wrapper_hosts | default('agent_config') }}"
    assert play.get("gather_facts") is False
    assert play.get("any_errors_fatal") is True
    assert play.get("max_fail_percentage") == 0

    pre_tasks = play.get("pre_tasks") or []
    assert any(
        (t.get("ansible.builtin.stat") or {}).get("path") == "{{ agent_install_dir }}"
        for t in pre_tasks
    ), "窄 playbook 必须在下发 wrapper 前校验 agent_install_dir 存在"
    for task in pre_tasks:
        assert "priv_wrapper" in (task.get("tags") or [])


def _non_comment_body(text: str) -> str:
    """注释可说明边界（与 #2218 配置面同款）；形态断言只扫无注释正文。"""
    return "\n".join(
        line
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def test_narrow_playbook_and_shared_tasks_exclude_update_and_restart_verbs():
    """范围隔离：窄 wrapper 通道不得夹带代码 rsync、服务重启或升级门禁。

    旧写法是裸 `assert 词 not in code`：YAML 一旦改名或这段逻辑搬走，它会**恒真**
    而继续报绿（#2639）。先以任务名证明扫的还是那段实现，再判退役形态。
    注释允许说明「不碰 rsync / upgrade-gate」等边界，故形态断言扫无注释正文。
    """
    for rel, anchor in (
        (_PRIV_PLAYBOOK_REL, _PRIV_PLAY_ANCHOR),
        (_SHARED_TASK_REL, _SHARED_TASK_ANCHOR),
    ):
        live = SourceGuard.of_repo_path(rel).anchored(anchor)
        guard = SourceGuard(_non_comment_body(live.text), origin=rel).anchored(anchor)
        for verb in _FORBIDDEN_UPDATE_VERBS:
            guard.assert_absent(
                verb,
                why=f"窄 wrapper 通道不得夹带更新/重启动词：{verb}",
            )


def test_priv_wrapper_shared_vars_single_definition_in_all_group_vars():
    """跨面变量集中在 group_vars/all.yml，不在 linux_hosts.yml 重复定义。"""
    all_vars = yaml.safe_load(GROUP_VARS_ALL.read_text(encoding="utf-8"))
    linux_vars = yaml.safe_load(GROUP_VARS_LINUX.read_text(encoding="utf-8"))
    for key in _SHARED_VAR_KEYS:
        assert key in all_vars, f"all.yml 缺少跨面共享变量 {key}"
        assert key not in linux_vars, f"linux_hosts.yml 仍重复定义 {key}（应只在 all.yml）"
