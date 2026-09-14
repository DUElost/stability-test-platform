from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest
import yaml

from tools.site_config.__main__ import main
from tools.site_config.validation import (
    MAX_CONFIG_BYTES,
    ConfigValidationError,
    load_site_config,
    parse_site_config,
    validate_config_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"


@pytest.fixture
def site_data():
    return {
        "schema_version": 1,
        "site": {"id": "synthetic-b", "display_name": "合成站点 B", "timezone": "Asia/Shanghai"},
        "platform": {"os_family": "linux", "cpu_arch": "x86_64", "service_manager": "systemd"},
        "network": {"dependency_mode": "offline"},
        "control_plane": {
            "target": "control.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "control_ssh",
            "deploy_root": "/opt/stp-control",
            "deploy_user": "stp",
            "public_url": "https://site.synthetic.invalid",
            "security_profile": "production",
            "tls_ref": "site_tls",
        },
        "storage": {
            "provisioning": "managed_linux",
            "protocol": "nfs",
            "target": "storage.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "storage_ssh",
            "share": "/srv/stp-export",
            "credential_ref": None,
            "mount_path": "/mnt/stp-share",
        },
        "agents": [{
            "key": "synthetic-agent-01",
            "target": "agent-01.synthetic.invalid",
            "os": {"distribution": "ubuntu", "version": "24.04"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "agent_ssh",
            "install_root": "/opt/stp-agent",
            "local_aee_root": "/var/lib/stp-aee",
        }],
        "dependencies": {"database_ref": "site_db", "redis_ref": "site_redis", "tools_profile": "synthetic_tools"},
        "security": {
            "jwt_key_ref": "site_jwt",
            "agent_secret_ref": "site_agent",
            "ssh_encryption_key_ref": "site_ssh_encryption",
            "initial_admin_ref": "site_admin",
        },
        "release": {"bundle": "/synthetic/not-read.bundle", "expected_release": "synthetic-2026.09.0"},
        "navigation": {"contact": "synthetic-ops", "documentation_url": "https://docs.synthetic.invalid/operations"},
    }


def parse_data(data):
    return parse_site_config(yaml.safe_dump(data, allow_unicode=True))


def rejected(data, code=None):
    with pytest.raises(ConfigValidationError) as caught:
        parse_data(data)
    checks = caught.value.checks
    if code is not None:
        assert code in {check.code for check in checks}
    return checks


def set_field(data, path, value):
    container = data
    for part in path[:-1]:
        container = container[part]
    container[path[-1]] = value


def write_config(tmp_path, site_data):
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(site_data, allow_unicode=True), encoding="utf-8")
    return path


def test_mixed_distributions_share_one_explicit_architecture(site_data):
    model = parse_data(site_data)
    assert model.platform.cpu_arch == "x86_64"
    assert model.control_plane.os.distribution == "debian"
    assert model.control_plane.os.version == "13"
    assert model.agents[0].os.distribution == "ubuntu"
    assert model.agents[0].os.version == "24.04"


@pytest.mark.parametrize("architecture", ["x86_64", "aarch64", "riscv64", "loongarch64"])
def test_declared_architecture_is_not_a_release_support_claim(site_data, tmp_path, architecture):
    site_data["platform"]["cpu_arch"] = architecture
    report = validate_config_file(write_config(tmp_path, site_data))
    assert report["status"] == "PASS"
    assert report["stage"] == "validate"
    assert {check["status"] for check in report["deferred_checks"]} == {"BLOCKED"}
    assert report["deferred_checks"][0]["check_id"] == "release.compatibility"


@pytest.mark.parametrize("field,value", [
    (("schema_version",), True),
    (("schema_version",), "1"),
    (("schema_version",), 1.0),
    (("schema_version",), 2),
    (("platform", "cpu_arch"), None),
    (("platform", "cpu_arch"), "unknown"),
    (("platform", "cpu_arch"), "$(uname -m)"),
    (("platform", "os_family"), "windows"),
    (("platform", "service_manager"), "auto"),
    (("control_plane", "os", "version"), 13),
    (("control_plane", "os", "version"), "stable"),
    (("control_plane", "os", "distribution"), "fedora"),
    (("agents", 0, "os", "version"), None),
    (("agents", 0, "os", "version"), 24.04),
    (("agents", 0, "os", "version"), "latest"),
    (("agents", 0, "os", "version"), "24.13"),
    (("network", "dependency_mode"), "auto"),
    (("control_plane", "deploy_user"), "root"),
    (("control_plane", "ssh_user"), "stp;id"),
    (("control_plane", "ssh_credential_ref"), "/run/secret-file"),
    (("security", "jwt_key_ref"), "${JWT_SECRET_KEY}"),
    (("security", "agent_secret_ref"), "secret://site/agent"),
    (("release", "bundle"), "https://downloads.synthetic.invalid/release.tar"),
    (("release", "expected_release"), "latest"),
    (("site", "timezone"), "Not/AZone"),
    (("site", "timezone"), "../../etc/passwd"),
    (("site", "display_name"), "\x1b[31mprivate"),
    (("navigation", "contact"), ""),
    (("agents",), []),
])
def test_strict_and_explicit_inputs(site_data, field, value):
    set_field(site_data, field, value)
    rejected(site_data)


@pytest.mark.parametrize("section", [
    (), ("site",), ("platform",), ("network",), ("control_plane",), ("control_plane", "os"),
    ("storage",), ("agents", 0), ("dependencies",), ("security",), ("release",), ("navigation",),
])
def test_unknown_fields_and_their_names_are_redacted(site_data, section):
    set_field(site_data, (*section, PRIVATE_MARKER), "another-private-value")
    checks = rejected(site_data, "extra_forbidden")
    serialized = json.dumps([asdict(check) for check in checks])
    assert PRIVATE_MARKER not in serialized
    assert "another-private-value" not in serialized
    assert "<unknown>" in serialized


@pytest.mark.parametrize("section,field", [
    (("security",), "jwt_secret_key"),
    (("control_plane",), "ssh_password"),
    (("dependencies",), "database_url"),
    (("agents", 0), "HOST_ID"),
    (("agents", 0, "os"), "cpu_arch"),
])
def test_secret_values_and_runtime_identity_fields_are_not_configuration(site_data, section, field):
    set_field(site_data, (*section, field), PRIVATE_MARKER)
    rejected(site_data, "extra_forbidden")


def test_missing_fields_do_not_inherit_defaults(site_data, monkeypatch):
    del site_data["platform"]["cpu_arch"]
    del site_data["control_plane"]["target"]
    monkeypatch.setenv("CPU_ARCH", "x86_64")
    monkeypatch.setenv("API_URL", "https://ambient.synthetic.invalid")
    checks = rejected(site_data, "missing")
    assert {check.location for check in checks} == {"$.platform.cpu_arch", "$.control_plane.target"}


@pytest.mark.parametrize("path", [
    "/", "/opt", "/var/lib", "/mnt", "/etc/stp", "/proc/stp", "relative/stp", "~/stp",
    "/opt/stp/../other", "/opt/./stp", "/opt//stp", "/opt/stp/", "/opt/stp name",
    "/opt/$(id)", "/opt/${HOME}", "/opt/{{root}}", "/opt/stp;id", "/opt/%n", "/opt/stp\nother",
])
def test_unsafe_deployment_paths_are_rejected(site_data, path):
    site_data["control_plane"]["deploy_root"] = path
    rejected(site_data)


@pytest.mark.parametrize("field,value,code", [
    (("agents", 0, "local_aee_root"), "/opt/stp-agent/data", "agent_local_path_overlap"),
    (("agents", 0, "local_aee_root"), "/opt/stp-agent", "agent_local_path_overlap"),
    (("agents", 0, "install_root"), "/var/lib/stp-aee/agent", "agent_local_path_overlap"),
    (("agents", 0, "local_aee_root"), "/mnt/stp-share", "shared_path_overlap"),
    (("agents", 0, "local_aee_root"), "/mnt/stp-share/local", "shared_path_overlap"),
    (("storage", "mount_path"), "/var/lib/stp-aee/shared", "shared_path_overlap"),
    (("storage", "mount_path"), "/opt/stp-control/shared", "shared_path_overlap"),
    (("control_plane", "deploy_root"), "/mnt/stp-share/control", "shared_path_overlap"),
    (("agents", 0, "install_root"), "/mnt/stp-share/agent", "shared_path_overlap"),
])
def test_local_installation_and_shared_storage_are_disjoint(site_data, field, value, code):
    set_field(site_data, field, value)
    rejected(site_data, code)


@pytest.mark.parametrize("url", [
    "https://user:password@site.synthetic.invalid", "https://site.synthetic.invalid?token=private",
    "https://site.synthetic.invalid?", "https://site.synthetic.invalid#private", "https://site.synthetic.invalid#",
    "https://site.synthetic.invalid/api", "javascript:alert(1)", "//site.synthetic.invalid",
    "https://site.synthetic.invalid:0", "https://site.synthetic.invalid:65536",
    "https://site.synthetic.invalid:", "https://site.synthetic.invalid:080",
    "https://[2001:db8::10]unexpected", "https://site.synthetic.invalid\n",
    "https://site.synthetic.invalid\\other", "https://site.synthetic.invalid/%0a",
    "https://${SITE_DOMAIN}", "https://{{site_domain}}", "https://站点.invalid",
])
def test_unsafe_origins_are_rejected(site_data, url):
    site_data["control_plane"]["public_url"] = url
    rejected(site_data)


@pytest.mark.parametrize("url", [
    "https://site.synthetic.invalid", "https://site.synthetic.invalid/",
    "https://site.synthetic.invalid:8443", "https://[2001:db8::10]:8443/",
])
def test_origin_shapes(site_data, url):
    site_data["control_plane"]["public_url"] = url
    assert parse_data(site_data).control_plane.public_url == url


def test_production_does_not_fall_back_to_http(site_data):
    site_data["control_plane"]["public_url"] = "http://site.synthetic.invalid"
    site_data["control_plane"]["tls_ref"] = None
    rejected(site_data, "production_https_required")
    assert site_data["control_plane"]["security_profile"] == "production"


def test_internal_http_requires_explicit_profile_and_no_tls_binding(site_data):
    site_data["control_plane"]["security_profile"] = "internal"
    site_data["control_plane"]["public_url"] = "http://site.synthetic.invalid"
    rejected(site_data, "http_tls_conflict")
    site_data["control_plane"]["tls_ref"] = None
    assert parse_data(site_data).control_plane.security_profile == "internal"


@pytest.mark.parametrize("profile", ["production", "internal"])
def test_https_always_requires_a_tls_reference(site_data, profile):
    site_data["control_plane"]["security_profile"] = profile
    site_data["control_plane"].pop("tls_ref")
    rejected(site_data, "tls_reference_required")


@pytest.mark.parametrize("target", [
    "localhost", "host.localhost", "127.0.0.1", "::1", "0.0.0.0", "::", "224.0.0.1",
    "169.254.1.1", "127.1", "2130706433", "0x7f000001", "0177.0.0.1", "::ffff:127.0.0.1",
    "ssh://agent.synthetic.invalid", "agent.synthetic.invalid:22", "user@agent.synthetic.invalid",
    "-oProxyCommand=id", "agent;id", "fe80::1%eth0", "a..invalid", "agent\nother",
])
def test_targets_are_explicit_bare_hostnames_or_addresses(site_data, target):
    site_data["agents"][0]["target"] = target
    rejected(site_data, "invalid_target")


@pytest.mark.parametrize("first,second", [
    ("AGENT-01.synthetic.invalid", "agent-01.synthetic.invalid."),
    ("2001:db8::10", "2001:0db8:0:0:0:0:0:10"),
    ("192.0.2.10", "::ffff:192.0.2.10"),
])
def test_duplicate_targets_use_normalized_spelling(site_data, first, second):
    site_data["agents"][0]["target"] = first
    another = copy.deepcopy(site_data["agents"][0])
    another.update(key="synthetic-agent-02", target=second)
    site_data["agents"].append(another)
    rejected(site_data, "duplicate_agent_target")


def test_duplicate_logical_keys_are_rejected(site_data):
    another = copy.deepcopy(site_data["agents"][0])
    another["target"] = "agent-02.synthetic.invalid"
    site_data["agents"].append(another)
    rejected(site_data, "duplicate_agent_key")


@pytest.mark.parametrize("role", ["control_plane", "storage"])
def test_initial_roles_have_distinct_targets(site_data, role):
    site_data[role]["target"] = site_data["agents"][0]["target"]
    rejected(site_data, "role_target_collision")


def test_security_bindings_are_not_accidentally_reused(site_data):
    site_data["security"]["agent_secret_ref"] = site_data["security"]["jwt_key_ref"]
    rejected(site_data, "secret_reference_collision")


def test_existing_share_does_not_authorize_server_management(site_data):
    storage = site_data["storage"]
    storage["provisioning"] = "existing_share"
    rejected(site_data, "existing_share_management_conflict")
    for field in ("os", "ssh_user", "ssh_credential_ref"):
        del storage[field]
    assert parse_data(site_data).storage.os is None


@pytest.mark.parametrize("field", ["os", "ssh_user", "ssh_credential_ref"])
def test_managed_storage_requires_explicit_management_inputs(site_data, field):
    site_data["storage"].pop(field)
    rejected(site_data, "storage_management_required")


def test_cifs_and_nfs_use_distinct_share_and_credential_shapes(site_data):
    site_data["storage"]["credential_ref"] = "storage_cifs"
    rejected(site_data, "nfs_credential_conflict")
    site_data["storage"].update(protocol="cifs", share="stp-aee", credential_ref=None)
    rejected(site_data, "cifs_credential_required")
    site_data["storage"]["credential_ref"] = "storage_cifs"
    assert parse_data(site_data).storage.share == "stp-aee"
    site_data["storage"]["share"] = "//storage.synthetic.invalid/stp-aee"
    rejected(site_data, "invalid_cifs_share")


@pytest.mark.parametrize("document,code", [
    ("schema_version: 1\nschema_version: 1\n", "yaml_duplicate_key"),
    ("site: {id: first, id: second}\n", "yaml_duplicate_key"),
    ("site:\n  id: first\n  'id': second\n", "yaml_duplicate_key"),
    ("shared: &defaults {name: synthetic}\nsite: *defaults\n", "yaml_reference"),
    ("site: &site {id: synthetic}\n", "yaml_reference"),
    ("site: &site [*site]\n", "yaml_reference"),
    ("site: {'<<': {id: synthetic}}\n", "yaml_reference"),
    ("true: private\n", "yaml_syntax"),
    ("? [private, key]\n: value\n", "yaml_syntax"),
    ("schema_version: 1\n---\nschema_version: 1\n", "yaml_syntax"),
    ("!!map [invalid]\n", "yaml_syntax"),
    ("!!int invalid\n", "yaml_syntax"),
    ("!!float invalid\n", "yaml_syntax"),
    ("!!timestamp 2026-99-99\n", "yaml_syntax"),
    ("!!python/object/apply:os.system [DO_NOT_EXECUTE]\n", "yaml_syntax"),
])
def test_yaml_ambiguity_and_unsafe_constructors_are_rejected(document, code, monkeypatch):
    def forbidden_command(*args, **kwargs):
        pytest.fail("YAML attempted command execution")

    monkeypatch.setattr(os, "system", forbidden_command)
    with pytest.raises(ConfigValidationError) as caught:
        parse_site_config(document)
    assert caught.value.checks[0].code == code


@pytest.mark.parametrize("document", ["", "null", "[]", "- site", "plain text", "1"])
def test_yaml_requires_a_configuration_mapping(document):
    with pytest.raises(ConfigValidationError):
        parse_site_config(document)


def test_input_size_and_depth_are_bounded():
    with pytest.raises(ConfigValidationError) as caught:
        parse_site_config(" " * (MAX_CONFIG_BYTES + 1))
    assert caught.value.checks[0].code == "input_size"
    document = "".join("  " * depth + "child:\n" for depth in range(40))
    with pytest.raises(ConfigValidationError) as caught:
        parse_site_config(document)
    assert caught.value.checks[0].code == "yaml_depth"


@pytest.mark.parametrize("json_output", [True, False])
@pytest.mark.parametrize("failure_kind", ["schema", "unknown_key", "syntax", "duplicate", "missing_file"])
def test_cli_errors_never_echo_source_values_or_filenames(site_data, tmp_path, capsys, json_output, failure_kind):
    path = tmp_path / f"{PRIVATE_MARKER}.yaml"
    if failure_kind == "schema":
        site_data["control_plane"]["public_url"] = f"https://admin:{PRIVATE_MARKER}@site.synthetic.invalid"
        path.write_text(yaml.safe_dump(site_data), encoding="utf-8")
    elif failure_kind == "unknown_key":
        site_data[PRIVATE_MARKER] = PRIVATE_MARKER
        path.write_text(yaml.safe_dump(site_data), encoding="utf-8")
    elif failure_kind == "syntax":
        path.write_text(f"site: [{PRIVATE_MARKER}\n", encoding="utf-8")
    elif failure_kind == "duplicate":
        path.write_text(f"{PRIVATE_MARKER}: first\n{PRIVATE_MARKER}: second\n", encoding="utf-8")
    arguments = ["validate", "--config", str(path)] + (["--json"] if json_output else [])
    assert main(arguments) == 1
    output = capsys.readouterr()
    assert PRIVATE_MARKER not in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert "FAIL" in output.out
    if json_output:
        assert json.loads(output.out)["status"] == "FAIL"


def test_success_report_does_not_serialize_the_inventory_or_bindings(site_data, tmp_path, capsys):
    site_data["site"]["display_name"] = PRIVATE_MARKER
    site_data["security"]["jwt_key_ref"] = PRIVATE_MARKER
    site_data["control_plane"]["target"] = f"{PRIVATE_MARKER.lower().replace('_', '-')}.invalid"
    path = write_config(tmp_path, site_data)
    assert main(["validate", "--config", str(path), "--json"]) == 0
    output = capsys.readouterr()
    assert PRIVATE_MARKER not in output.out + output.err
    assert site_data["control_plane"]["target"] not in output.out
    assert site_data["release"]["bundle"] not in output.out
    assert json.loads(output.out)["checks"][0]["status"] == "PASS"


@pytest.mark.parametrize("arguments", [
    [], ["validate"], ["install"], ["preflight"], ["plan"], [PRIVATE_MARKER],
    ["validate", "--config", "site.yaml", "--password", PRIVATE_MARKER],
])
def test_only_explicit_validate_is_exposed_and_argument_errors_are_redacted(arguments, capsys):
    with pytest.raises(SystemExit) as caught:
        main(arguments)
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert PRIVATE_MARKER not in output.out + output.err


@pytest.mark.parametrize("input_kind", ["directory", "symlink", "fifo", "invalid_utf8", "too_large"])
def test_only_bounded_regular_utf8_inputs_are_read(tmp_path, input_kind):
    path = tmp_path / "input.yaml"
    expected_code = "input_file"
    if input_kind == "directory":
        path.mkdir()
    elif input_kind == "symlink":
        target = tmp_path / "not-a-config"
        target.write_text(PRIVATE_MARKER, encoding="utf-8")
        path.symlink_to(target)
    elif input_kind == "fifo":
        os.mkfifo(path)
    elif input_kind == "invalid_utf8":
        path.write_bytes(b"\xff\xfe")
        expected_code = "input_encoding"
    else:
        path.write_bytes(b" " * (MAX_CONFIG_BYTES + 1))
        expected_code = "input_size"
    report = validate_config_file(path)
    assert report["status"] == "FAIL"
    assert report["checks"][0]["code"] == expected_code
    assert PRIVATE_MARKER not in json.dumps(report)


def test_io_exception_messages_are_not_reported(tmp_path, monkeypatch):
    def forbidden_open(*args, **kwargs):
        raise PermissionError(PRIVATE_MARKER)

    monkeypatch.setattr(os, "open", forbidden_open)
    report = validate_config_file(tmp_path / "site.yaml")
    assert report["checks"][0]["code"] == "input_file"
    assert PRIVATE_MARKER not in json.dumps(report)


def test_repository_example_intentionally_requires_remaining_site_inputs():
    report = validate_config_file(REPO_ROOT / "deploy/sites/site.example.yaml")
    assert report["status"] == "FAIL"
    locations = {check["location"] for check in report["checks"]}
    assert "$.platform.cpu_arch" in locations
    assert "$.agents[0].os.version" in locations
    assert "$.network.dependency_mode" in locations


def test_configuration_validation_has_no_runtime_imports_network_or_writes(site_data, tmp_path):
    path = write_config(tmp_path, site_data)
    for filename in (".env", ".env.backend", "inventory.yml", "not-read.bundle"):
        (tmp_path / filename).write_text(PRIVATE_MARKER, encoding="utf-8")
    script = textwrap.dedent("""
        import os
        import sys

        def audit(event, arguments):
            if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
                raise AssertionError("Configuration validation attempted networking or a command")
            if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.chmod", "os.symlink"}:
                raise AssertionError("Configuration validation attempted a filesystem mutation")
            if event == "open":
                filename, mode, flags = arguments
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    raise AssertionError("Configuration validation attempted a file write")
                if isinstance(filename, str) and os.path.basename(filename) in {
                    ".env", ".env.backend", "inventory.yml", "not-read.bundle",
                }:
                    raise AssertionError("Configuration validation attempted implicit input or bundle access")

        sys.addaudithook(audit)
        from tools.site_config.__main__ import main
        result = main(["validate", "--config", sys.argv[1], "--json"])
        forbidden = ("backend", "dotenv", "sqlalchemy", "psycopg", "redis", "paramiko", "requests")
        assert not any(name.split(".")[0] in forbidden for name in sys.modules)
        raise SystemExit(result)
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(path)],
        cwd=tmp_path,
        env={
            "PATH": os.defpath,
            "PYTHONPATH": str(REPO_ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": "postgresql://unused.synthetic.invalid/do_not_connect",
            "API_URL": "https://unused.synthetic.invalid",
        },
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert PRIVATE_MARKER not in result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "PASS"
    assert load_site_config(path).site.id == "synthetic-b"
