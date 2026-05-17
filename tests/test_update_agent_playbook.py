from pathlib import Path

import yaml


PLAYBOOK = Path("tools/ansible/playbooks/update_agent.yml")


def _tasks():
    plays = yaml.safe_load(PLAYBOOK.read_text(encoding="utf-8"))
    tasks = []
    for play in plays:
        tasks.extend(play.get("pre_tasks", []))
        tasks.extend(play.get("tasks", []))
    return tasks


def test_update_agent_syncs_directly_without_remote_staging_copy():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "Copy latest agent source tree to remote temp directory" not in text
    assert "agent_remote_tmp_dir" not in text
    assert "ansible.builtin.copy:\n        src: \"{{ agent_source_dir }}/\"" not in text


def test_update_agent_previews_changes_before_syncing_or_restarting():
    text = PLAYBOOK.read_text(encoding="utf-8")
    task_names = {task.get("name") for task in _tasks()}

    assert "Preview agent code changes with rsync dry-run" in task_names
    assert "Preview agentctl changes with rsync dry-run" in task_names
    assert "rsync" in text
    assert "--dry-run" in text
    assert "--itemize-changes" in text
    assert "--delete-excluded" in text
    assert "{{ agent_source_dir }}/" in text
    assert "{{ agent_install_dir }}/agent/" in text


def test_update_agent_only_backs_up_and_restarts_when_changes_exist():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "agent_code_change_lines | length > 0" in text
    assert "agentctl_change_lines | length > 0" in text
    assert "agent_env_changed | bool" in text
    assert "agent_update_requires_restart | bool" in text
    assert "reject('match', '^\\\\.[^ ]\\\\s{10}')" in text
    assert "reject('match', '^\\\\.[fd]\\\\.\\\\.t\\\\.\\\\.\\\\.\\\\.\\\\.\\\\.\\\\s')" in text
    assert "__pycache__/" in text
    assert "\\\\.pyc$" in text


def test_update_agent_uses_stable_backup_timestamp():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert 'agent_update_timestamp: "{{ ansible_date_time.iso8601_basic_short' in text
    assert 'agent_backup_dir: "{{ agent_install_dir }}/agent.bak.{{ agent_update_timestamp }}"' in text
    assert (
        'agent_agentctl_backup_path: "{{ agent_install_dir }}/agentctl.bak.{{ agent_update_timestamp }}"'
        in text
    )
    assert (
        'agent_service_backup_path: "/etc/systemd/system/{{ agent_service_name }}.service.bak.{{ agent_update_timestamp }}"'
        in text
    )


def test_update_agent_previews_and_syncs_service_unit():
    text = PLAYBOOK.read_text(encoding="utf-8")
    task_names = {task.get("name") for task in _tasks()}

    assert "Preview service unit changes with rsync dry-run" in task_names
    assert "Refresh systemd service unit from local source" in text
    assert "Snapshot current service unit before sync" in text
    assert "Roll back service unit from snapshot" in text
    assert "{{ agent_source_dir }}/stability-test-agent.service" in text
    assert "/etc/systemd/system/{{ agent_service_name }}.service" in text
    assert "agent_service_change_lines | length > 0" in text


def test_update_agent_reenables_service_on_restart_and_rollback():
    text = PLAYBOOK.read_text(encoding="utf-8")

    assert "Reload systemd and restart service" in text
    assert "Restart service after rollback" in text
    assert "enabled: true" in text
