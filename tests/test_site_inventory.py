from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from tools.site_config.inventory import (
    DEFAULT_CREDENTIAL_REF,
    InventoryError,
    TEMPLATE,
    materialize_bindings,
    merge_agents,
    parse_inventory,
)
from tools.site_config.validation import ConfigValidationError, load_site_config, parse_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"

# 一套共享凭据：两行同 kind，才能共用一个 ssh_credential_ref
INVENTORY = """\
# 站点 Agent 清单
[stp_agents]
192.0.2.11 ansible_host=192.0.2.11 ansible_user=ops ansible_password={secret}
192.0.2.12 ansible_host=192.0.2.12 ansible_user=ops ansible_password={secret}

[stp_agents:vars]
install_root=/opt/stability-test-agent
local_aee_root=/var/stp-aee
"""

KEY_INVENTORY = """\
[stp_agents]
192.0.2.12 ansible_host=192.0.2.12 ansible_user=ops ansible_ssh_private_key_file=/root/.ssh/id_ed25519
"""


def write_inventory(tmp_path: Path, text: str = INVENTORY) -> Path:
    path = tmp_path / "hosts.ini"
    path.write_text(text.format(secret=PRIVATE_MARKER), encoding="utf-8")
    return path


def site_yaml(tmp_path: Path, agents: list[dict] | None = None) -> Path:
    """一份最小可用的 site.yaml：存储用 local_mount（本机子树）。"""
    documents = {
        "schema_version": 1,
        "site": {"id": "city-b", "display_name": "站点 B", "timezone": "Asia/Shanghai"},
        "platform": {"os_family": "linux", "cpu_arch": "x86_64", "service_manager": "systemd"},
        "network": {"dependency_mode": "offline"},
        "control_plane": {
            "target": "city-b.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": None,
            "ssh_credential_ref": None,
            "deploy_root": str(tmp_path / "opt/stp-city-b"),
            "deploy_user": "stp",
            "public_url": "http://192.0.2.1",
            "security_profile": "internal",
            "tls_ref": None,
        },
        "storage": {
            "provisioning": "local_mount", "protocol": None, "target": None, "os": None,
            "ssh_user": None, "ssh_credential_ref": None, "share": None, "credential_ref": None,
            "mount_path": str(tmp_path / "srv/stp-aee"),
        },
        "agents": agents or [],
        "dependencies": {
            "database_ref": "site_database", "redis_ref": "site_redis", "tools_profile": "site_tools",
        },
        "security": {
            "jwt_key_ref": "site_jwt", "agent_secret_ref": "site_agent_secret",
            "ssh_encryption_key_ref": "site_ssh_encryption", "initial_admin_ref": "site_admin",
        },
        "release": {"bundle": str(tmp_path / "bundle"), "manifest": None, "expected_release": "local-20260915"},
        "navigation": {"contact": "ops@example.invalid", "documentation_url": "https://docs.example.invalid/site-ops"},
    }
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(documents, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_template_is_a_usable_starting_point(tmp_path):
    path = tmp_path / "hosts.ini"
    path.write_text(TEMPLATE, encoding="utf-8")
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    # 模板里只有注释与组级默认：没有主机时必须拒绝，而不是装出个空站点
    assert caught.value.code == "inventory_empty"
    assert "[stp_agents]" in TEMPLATE
    assert "install_root" in TEMPLATE


def test_parse_keeps_group_defaults_and_per_host_overrides(tmp_path):
    path = write_inventory(tmp_path)
    entries, credentials = parse_inventory(path)
    assert [entry["target"] for entry in entries] == ["192.0.2.11", "192.0.2.12"]
    assert {entry["install_root"] for entry in entries} == {"/opt/stability-test-agent"}
    assert {entry["ssh_credential_ref"] for entry in entries} == {DEFAULT_CREDENTIAL_REF}
    # 一套共享凭据：两台机器同 ref 同 material 才允许合并
    assert set(credentials) == {DEFAULT_CREDENTIAL_REF}
    assert credentials[DEFAULT_CREDENTIAL_REF]["USERNAME"] == "ops"
    assert credentials[DEFAULT_CREDENTIAL_REF]["PASSWORD"] == PRIVATE_MARKER


def test_per_host_credential_ref_is_honoured(tmp_path):
    """逐台覆盖：私钥型主机必须自带 ref，否则会撞上共享口令绑定。"""
    path = write_inventory(tmp_path, KEY_INVENTORY.replace(
        "ansible_ssh_private_key_file=/root/.ssh/id_ed25519",
        "ansible_ssh_private_key_file=/root/.ssh/id_ed25519 ssh_credential_ref=agent_ssh_b",
    ))
    entries, credentials = parse_inventory(path)
    assert {entry["ssh_credential_ref"] for entry in entries} == {"agent_ssh_b"}
    assert credentials["agent_ssh_b"]["PRIVATE_KEY_PATH"] == "/root/.ssh/id_ed25519"


def test_same_ref_with_two_different_secrets_is_refused(tmp_path):
    """同 ref 不同凭据直接拒绝：否则某台机器的凭据会被静默写错。"""
    path = write_inventory(tmp_path, INVENTORY.replace(
        "192.0.2.12 ansible_host=192.0.2.12 ansible_user=ops ansible_password={secret}",
        "192.0.2.12 ansible_host=192.0.2.12 ansible_user=ops ansible_password=other-secret",
    ))
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    assert caught.value.code == "inventory_credential_conflict"
    assert "192.0.2.12" in caught.value.detail


def test_an_explicit_agent_key_must_still_be_a_logical_key(tmp_path):
    path = write_inventory(tmp_path, KEY_INVENTORY.replace(
        "ansible_host=192.0.2.12", "agent_key=Agent_12 ansible_host=192.0.2.12",
    ))
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    assert caught.value.code == "inventory_shape"
    assert "agent_key" in caught.value.detail


def test_credential_kinds_cannot_share_one_ref(tmp_path):
    """口令型主机 + 私钥型主机共用一个 ref = 后者会覆盖前者的凭据。"""
    path = write_inventory(tmp_path, """\
[stp_agents]
192.0.2.11 ansible_user=ops ansible_password={secret}
192.0.2.12 ansible_user=ops ansible_ssh_private_key_file=/root/.ssh/id_ed25519
""")
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    assert caught.value.code == "inventory_credential_conflict"
    assert "192.0.2.12" in caught.value.detail


@pytest.mark.parametrize("bad_line, code, hint", [
    ("192.0.2.13 ansible_user=ops", "inventory_credential_missing", "192.0.2.13"),
    ("192.0.2.13 ansible_password=x", "inventory_user_missing", "192.0.2.13"),
    ("192.0.2.13 ansible_user=ops ansible_password=x unknown_key=1", "inventory_shape", "unknown_key"),
])
def test_bad_lines_fail_closed(tmp_path, bad_line, code, hint):
    path = write_inventory(tmp_path, f"[stp_agents]\n{bad_line}\n")
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    assert caught.value.code == code
    assert hint in caught.value.detail


def test_a_malformed_token_never_echoes_its_value(tmp_path):
    """裸词本身就是秘密（口令里带空格）：错误详情一个字都不能回显。"""
    path = write_inventory(tmp_path, f"[stp_agents]\n192.0.2.13 ansible_user=ops {PRIVATE_MARKER}\n")
    with pytest.raises(InventoryError) as caught:
        parse_inventory(path)
    assert caught.value.code == "inventory_shape"
    assert PRIVATE_MARKER not in caught.value.detail
    assert PRIVATE_MARKER not in str(caught.value)


def test_missing_inventory_file_is_reported_not_raised_raw(tmp_path):
    with pytest.raises(InventoryError) as caught:
        parse_inventory(tmp_path / "absent.ini")
    assert caught.value.code == "inventory_file"


def test_other_groups_are_ignored(tmp_path):
    path = write_inventory(tmp_path, INVENTORY + "\n[other]\n192.0.2.9 ansible_user=nobody\n")
    entries, _ = parse_inventory(path)
    assert [entry["target"] for entry in entries] == ["192.0.2.11", "192.0.2.12"]


def test_merge_agent_hosts_into_the_declared_config(tmp_path):
    config_path = site_yaml(tmp_path)
    config = load_site_config(config_path)
    merged = merge_agents(config, write_inventory(tmp_path))
    assert [agent.target for agent in merged.agents] == ["192.0.2.11", "192.0.2.12"]
    # 逻辑键首字符必须是字母：IP 目标派生成 agent-192-0-2-11
    assert {agent.key for agent in merged.agents} == {"agent-192-0-2-11", "agent-192-0-2-12"}
    # 合并结果必须仍是同一套 Pydantic 约束下的对象（不绕过模型）
    assert merged.control_plane.deploy_root == config.control_plane.deploy_root
    assert merged.storage.provisioning == "local_mount"


def test_merge_lets_the_inventory_win_on_the_same_target(tmp_path):
    config_path = site_yaml(tmp_path, agents=[{
        "key": "legacy",
        "target": "192.0.2.11",
        "os": {"distribution": "debian", "version": "13"},
        "ssh_user": "old",
        "ssh_credential_ref": "legacy_ssh",
        "install_root": "/opt/stability-test-agent",
        "local_aee_root": "/var/stp-aee",
    }])
    config = load_site_config(config_path)
    merged = merge_agents(config, write_inventory(tmp_path))
    by_target = {agent.target: agent for agent in merged.agents}
    assert by_target["192.0.2.11"].ssh_user == "ops"
    assert by_target["192.0.2.11"].ssh_credential_ref == DEFAULT_CREDENTIAL_REF
    assert "legacy" not in {agent.key for agent in merged.agents}


def test_merged_config_is_still_rejected_when_the_inventory_mixes_install_roots(tmp_path):
    """inventory 不能绕过站点约束：拒绝方式与其它配置失败一致（检查项，不是异常）。"""
    path = write_inventory(tmp_path, INVENTORY.replace(
        "192.0.2.12 ansible_host=192.0.2.12", "192.0.2.12 install_root=/opt/other-root ansible_host=192.0.2.12",
    ))
    with pytest.raises(ConfigValidationError) as caught:
        merge_agents(load_site_config(site_yaml(tmp_path)), path)
    assert {check.code for check in caught.value.checks} == {"agent_install_root_mismatch"}
    assert all(check.status == "FAIL" for check in caught.value.checks)


def test_materialize_writes_owner_only_bindings_and_never_echoes_values(tmp_path):
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o700)
    written = materialize_bindings(write_inventory(tmp_path), bindings)
    assert written == [DEFAULT_CREDENTIAL_REF]
    path = bindings / DEFAULT_CREDENTIAL_REF
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    assert PRIVATE_MARKER in path.read_text(encoding="utf-8")
    assert PRIVATE_MARKER not in " ".join(written)


def test_materialize_refuses_a_loose_bindings_directory(tmp_path):
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o755)
    with pytest.raises(InventoryError) as caught:
        materialize_bindings(write_inventory(tmp_path), bindings)
    assert caught.value.code == "binding_dir"
    assert not (bindings / DEFAULT_CREDENTIAL_REF).exists()


def test_materialize_dry_run_writes_nothing(tmp_path):
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o700)
    assert materialize_bindings(write_inventory(tmp_path), bindings, dry_run=True) == [DEFAULT_CREDENTIAL_REF]
    assert list(bindings.iterdir()) == []


def test_inventory_derived_config_survives_a_round_trip(tmp_path):
    """inventory → agents 的结果必须能重新序列化回站点配置形状。"""
    merged = merge_agents(load_site_config(site_yaml(tmp_path)), write_inventory(tmp_path))
    payload = merged.model_dump()
    assert payload["agents"][0]["os"] == {"distribution": "debian", "version": "13"}
    reparsed = parse_site_config(yaml.safe_dump(payload))
    assert [agent.target for agent in reparsed.agents] == ["192.0.2.11", "192.0.2.12"]


def test_ansible_port_survives_into_the_agent_entry(tmp_path):
    """#2283：ansible_port 此前解析后被 pop 丢弃——Host 行恒以 22 建立。"""
    text = INVENTORY.replace(
        "192.0.2.11 ansible_host=192.0.2.11 ansible_user=ops",
        "192.0.2.11 ansible_host=192.0.2.11 ansible_port=2222 ansible_user=ops",
    )
    entries, _credentials = parse_inventory(write_inventory(tmp_path, text))

    by_target = {entry["target"]: entry for entry in entries}
    assert by_target["192.0.2.11"]["ssh_port"] == 2222
    assert by_target["192.0.2.12"]["ssh_port"] == 22, "未声明端口的主机取默认 22"


def test_ansible_port_must_be_a_valid_port(tmp_path):
    """越界端口在**解析期**拒绝，而不是建出一条连不上的 Host（#2283）。"""
    text = INVENTORY.replace(
        "192.0.2.11 ansible_host=192.0.2.11 ansible_user=ops",
        "192.0.2.11 ansible_host=192.0.2.11 ansible_port=70000 ansible_user=ops",
    )
    with pytest.raises(InventoryError) as exc:
        parse_inventory(write_inventory(tmp_path, text))
    assert "ansible_port" in str(exc.value)

    text = INVENTORY.replace(
        "192.0.2.11 ansible_host=192.0.2.11 ansible_user=ops",
        "192.0.2.11 ansible_host=192.0.2.11 ansible_port=abc ansible_user=ops",
    )
    with pytest.raises(InventoryError):
        parse_inventory(write_inventory(tmp_path, text))
