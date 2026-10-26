"""Host journal configuration must cover both provisioning and existing hosts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ANSIBLE = ROOT / "tools/ansible"
TASKS = ANSIBLE / "roles/agent_deploy/tasks/journald.yml"


def test_all_agent_entrypoints_use_the_same_journal_baseline():
    for name in ("configure_agents.yml", "install_agent.yml", "update_agent.yml", "configure_journald.yml"):
        plays = yaml.safe_load((ANSIBLE / "playbooks" / name).read_text())
        included = [
            task
            for play in plays
            for task in play.get("tasks", [])
            if "agent_deploy/tasks/journald.yml"
            in task.get("ansible.builtin.include_tasks", "")
        ]
        assert len(included) == 1, name
        assert "journald" in included[0]["tags"]


def test_scoped_rollout_executes_every_task_and_never_restarts_the_agent():
    tasks = yaml.safe_load(TASKS.read_text())
    restarts = []
    for task in tasks:
        assert "journald" in task.get("tags", [])
        service = task.get("ansible.builtin.systemd", {})
        if service.get("state"):
            restarts.append(task)
    assert len(restarts) == 1
    restart = restarts[0]
    assert restart["ansible.builtin.systemd"] == {
        "name": "systemd-journald", "state": "restarted",
    }
    assert "not ansible_check_mode" in restart["when"]
    assert "journald_budget_file.changed" in restart["when"]
    assert not any(
        "--vacuum" in str(task) or "--rotate" in str(task)
        for task in tasks
    )


def test_effective_configuration_is_checked_before_activation():
    tasks = yaml.safe_load(TASKS.read_text())
    config_check = next(
        task for task in tasks
        if task.get("register") == "journald_effective_config"
    )
    activation = next(task for task in tasks if "ansible.builtin.systemd" in task)
    assert tasks.index(config_check) < tasks.index(activation)
    for key in ("SystemMaxUse", "SystemKeepFree"):
        assert key in config_check["failed_when"]
    guard = next(task for task in tasks if task.get("register") == "journald_guard_after")
    assert "journald_guard_before.stdout" in guard["failed_when"]


def test_new_configuration_directory_is_safe_in_check_mode():
    tasks = yaml.safe_load(TASKS.read_text())
    copy = next(task for task in tasks if "ansible.builtin.copy" in task)
    assert "journald_directory_before.stat.exists" in copy["when"]
    assert "not ansible_check_mode" in copy["when"]
    root_probe = next(task for task in tasks if task.get("register") == "journald_root_capacity")
    assert root_probe["check_mode"] is False
    assert root_probe["changed_when"] is False
