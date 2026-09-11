import configparser
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
ANSIBLE_DIR = REPO_ROOT / "tools" / "ansible"
ANSIBLE_CFG = ANSIBLE_DIR / "ansible.cfg"
UPDATE_PLAYBOOK = ANSIBLE_DIR / "playbooks" / "update_agent.yml"
RUNBOOK = REPO_ROOT / "docs" / "linux-agent-ansible-runbook.md"


def test_ansible_cfg_enables_host_key_checking():
    parser = configparser.ConfigParser()
    parser.read(ANSIBLE_CFG, encoding="utf-8")

    assert parser.getboolean("defaults", "host_key_checking") is True


def _rsync_ssh_command():
    plays = yaml.safe_load(UPDATE_PLAYBOOK.read_text(encoding="utf-8"))
    for play in plays:
        command = (play.get("vars") or {}).get("agent_rsync_ssh_command")
        if command:
            return command
    raise AssertionError("agent_rsync_ssh_command not found")


def test_update_agent_rsync_does_not_bypass_host_key_verification():
    command = _rsync_ssh_command()

    assert "StrictHostKeyChecking" not in command
    assert "UserKnownHostsFile" not in command
    assert "ConnectTimeout" in command


def test_runbook_documents_first_connect_and_key_rotation_flow():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "host_key_checking = True" in text
    assert "首次连接前登记" in text
    assert "ssh-keyscan" in text
    assert "换钥" in text
    assert "ssh-keygen -R" in text
