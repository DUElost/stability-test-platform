from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from tools.site_config.__main__ import main
from tools.site_config.manifest import parse_release_manifest
from tools.site_config.plan import PLAN_REPORT_NAME, TEMPLATE_PLAN, plan_site_report
from tools.site_config.validation import ConfigValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"


def site_fixture(manifest_path: str | None) -> dict:
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
        "release": {
            "bundle": "/synthetic/not-read.bundle",
            "manifest": manifest_path,
            "expected_release": "synthetic-2026.09.0",
        },
        "navigation": {"contact": "synthetic-ops", "documentation_url": "https://docs.synthetic.invalid/operations"},
    }


def manifest_fixture() -> dict:
    return {
        "manifest_version": 1,
        "product": {"version": "synthetic-2026.09.0"},
        "source": {"revision": "0123456789abcdef0123456789abcdef01234567"},
        "components": [
            {"name": "agent-code", "digest": "sha256:" + "a" * 64},
            {"name": "host-resources", "digest": "sha256:" + "b" * 64},
            {"name": "control-plane", "digest": "sha256:" + "c" * 64},
        ],
        "database": {"schema_target": "4c84155b7e59"},
        "compatibility": {
            "agent_protocol": ">=1.0,<2.0",
            "platforms": [
                {"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]},
                {"distribution": "ubuntu", "versions": ["24.04"], "cpu_arch": ["x86_64"]},
            ],
        },
        "provenance": {"attestation": "controlled_channel", "evidence_ref": "site_release_channel"},
    }


def write_plan_inputs(tmp_path: Path, *, manifest: dict | None = None, manifest_text: str | None = None):
    manifest_path = tmp_path / "manifest.json"
    if manifest_text is not None:
        manifest_path.write_text(manifest_text, encoding="utf-8")
    else:
        manifest_path.write_text(json.dumps(manifest if manifest is not None else manifest_fixture()), encoding="utf-8")
    site_path = tmp_path / "site.yaml"
    site_path.write_text(yaml.safe_dump(site_fixture(str(manifest_path)), allow_unicode=True), encoding="utf-8")
    return site_path, manifest_path


def codes(report: dict) -> set[str]:
    return {check["code"] for check in report["checks"]}


def test_plan_requires_a_declared_manifest(tmp_path):
    site_path = tmp_path / "site.yaml"
    site_path.write_text(yaml.safe_dump(site_fixture(None), allow_unicode=True), encoding="utf-8")
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    assert report["stage"] == "plan"
    assert "manifest_missing" in codes(report)


@pytest.mark.parametrize("kind", ["missing", "directory", "invalid_utf8"])
def test_plan_rejects_unreadable_manifests(tmp_path, kind):
    site_path, manifest_path = write_plan_inputs(tmp_path)
    if kind == "missing":
        manifest_path.unlink()
    elif kind == "directory":
        manifest_path.unlink()
        manifest_path.mkdir()
    else:
        manifest_path.write_bytes(b"\xff\xfe")
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    assert "manifest_input" in codes(report)


@pytest.mark.parametrize(("manifest_text", "code"), [
    ('{"manifest_version": 1, "product": {"version": "x"}, "product": {"version": "y"}}', "manifest_syntax"),
    ("not json", "manifest_syntax"),
    ("[1, 2, 3]", "manifest_syntax"),
])
def test_plan_rejects_invalid_json_manifests(tmp_path, manifest_text, code):
    site_path, _ = write_plan_inputs(tmp_path, manifest_text=manifest_text)
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    assert code in codes(report)


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda m: m.update({"unexpected": True}), "extra_forbidden"),
    (lambda m: m["components"][0].update({"digest": "sha256:" + "A" * 64}), "manifest_digest"),
    (lambda m: m.pop("provenance"), "missing"),
    (lambda m: m.update({"components": [{"name": "control-plane", "digest": "sha256:" + "c" * 64}]}),
     "release_components_missing"),
])
def test_plan_rejects_malformed_manifests(tmp_path, mutate, code):
    manifest = manifest_fixture()
    mutate(manifest)
    site_path, _ = write_plan_inputs(tmp_path, manifest=manifest)
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    assert code in codes(report)


def test_plan_blocks_on_release_version_mismatch(tmp_path):
    manifest = manifest_fixture()
    manifest["product"]["version"] = "synthetic-2026.10.0"
    site_path, _ = write_plan_inputs(tmp_path, manifest=manifest)
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    assert "release_version_mismatch" in codes(report)


def test_plan_blocks_when_a_role_os_is_outside_the_support_matrix(tmp_path):
    manifest = manifest_fixture()
    manifest["compatibility"]["platforms"] = [
        {"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]},
    ]
    site_path, _ = write_plan_inputs(tmp_path, manifest=manifest)
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    failures = [check for check in report["checks"] if check["check_id"] == "release.platform"]
    assert failures and failures[0]["role"] == "agent"
    assert failures[0]["location"] == "$.agents[0].os"


def test_plan_lists_templates_and_steps_without_values(tmp_path):
    site_path, _ = write_plan_inputs(tmp_path)
    report = plan_site_report(site_path)
    assert report["status"] == "PASS"
    assert report["release"]["product_version_matched"] is True
    assert report["release"]["attestation_verified"] is False
    assert report["release"]["components"] == ["agent-code", "control-plane", "host-resources"]
    https_entry = next(entry for entry in report["template_changes"] if entry["template"].endswith("-https.conf"))
    placeholders = {item["name"]: item["resolved"] for item in https_entry["placeholders"]}
    assert placeholders == {
        "<deploy-root>": True,
        "<server-name>": True,
        "<tls-cert-path>": False,
        "<tls-key-path>": False,
    }
    steps = {step["step"]: step for step in report["steps"]}
    assert steps["install_agents"]["agent_count"] == 1
    assert steps["render_control_plane_templates"]["template_count"] == len(TEMPLATE_PLAN)
    assert steps["render_control_plane_templates"]["pending_binding_placeholders"] == 2
    assert "verification_deferred" in codes(report)


def test_plan_report_never_echoes_inputs(tmp_path, capsys):
    data = site_fixture(str(tmp_path / "manifest.json"))
    data["site"]["display_name"] = PRIVATE_MARKER
    data["control_plane"]["target"] = "private-target.synthetic.invalid"
    data["control_plane"]["deploy_root"] = "/opt/private-deploy-root"
    data["storage"]["share"] = f"/srv/{PRIVATE_MARKER.lower()}"
    manifest = manifest_fixture()
    manifest["provenance"]["evidence_ref"] = PRIVATE_MARKER
    site_path, manifest_path = (
        tmp_path / "site.yaml",
        tmp_path / "manifest.json",
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    site_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    assert main(["plan", "--config", str(site_path), "--json"]) == 0
    output = capsys.readouterr()
    assert PRIVATE_MARKER not in output.out + output.err
    assert "private-target.synthetic.invalid" not in output.out
    assert "/opt/private-deploy-root" not in output.out
    assert str(manifest_path) not in output.out


def test_plan_report_is_written_once_with_owner_only_permissions(tmp_path, capsys):
    site_path, _ = write_plan_inputs(tmp_path)
    save_dir = tmp_path / "reports"
    save_dir.mkdir()
    assert main(["plan", "--config", str(site_path), "--save-dir", str(save_dir), "--json"]) == 0
    report_file = save_dir / PLAN_REPORT_NAME
    assert report_file.is_file()
    assert stat_mode(report_file) == 0o600
    assert json.loads(report_file.read_text(encoding="utf-8"))["status"] == "PASS"
    capsys.readouterr()
    assert main(["plan", "--config", str(site_path), "--save-dir", str(save_dir), "--json"]) == 1
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "FAIL"
    assert "output_dir_exists" in {check["code"] for check in second["checks"]}


@pytest.mark.parametrize("kind", ["missing", "symlink", "file"])
def test_plan_refuses_unsafe_save_dirs(tmp_path, capsys, kind):
    site_path, _ = write_plan_inputs(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    if kind == "missing":
        save_dir = tmp_path / "absent"
    elif kind == "symlink":
        save_dir = tmp_path / "link"
        save_dir.symlink_to(target)
    else:
        save_dir = tmp_path / "plain-file"
        save_dir.write_text("x", encoding="utf-8")
    assert main(["plan", "--config", str(site_path), "--save-dir", str(save_dir), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "output_dir" in {check["code"] for check in report["checks"]}


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


@pytest.mark.parametrize("template_entry", TEMPLATE_PLAN, ids=lambda entry: entry["template"])
def test_every_planned_placeholder_exists_in_its_template(template_entry):
    template_path = REPO_ROOT / template_entry["template"]
    text = template_path.read_text(encoding="utf-8")
    for placeholder in template_entry["placeholders"]:
        assert placeholder["name"] in text, f"{placeholder['name']} missing from {template_entry['template']}"


def test_one_template_set_renders_two_synthetic_sites():
    template = (REPO_ROOT / "deploy/control-plane/nginx/stability-platform-https.conf").read_text(encoding="utf-8")
    sites = {
        "a": {
            "<deploy-root>": "/opt/site-a",
            "<deploy-user>": "stpa",
            "<server-name>": "a.synthetic.invalid",
            "<tls-cert-path>": "/etc/stp/tls/a/fullchain.pem",
            "<tls-key-path>": "/etc/stp/tls/a/privkey.pem",
        },
        "b": {
            "<deploy-root>": "/srv/site-b",
            "<deploy-user>": "stpb",
            "<server-name>": "b.synthetic.invalid",
            "<tls-cert-path>": "/etc/stp/tls/b/fullchain.pem",
            "<tls-key-path>": "/etc/stp/tls/b/privkey.pem",
        },
    }
    rendered = {}
    for name, substitutions in sites.items():
        text = template
        for placeholder, value in substitutions.items():
            text = text.replace(placeholder, value)
        rendered[name] = text
    for name, text in rendered.items():
        assert "<" not in text, f"unresolved placeholder in {name}"
        assert sites[name]["<server-name>"] in text
        assert sites[name]["<tls-cert-path>"] in text
        assert "client_max_body_size 25m;" in text
    assert sites["a"]["<server-name>"] not in rendered["b"]
    assert sites["b"]["<server-name>"] not in rendered["a"]
    assert sites["b"]["<tls-cert-path>"] not in rendered["a"]


def test_plan_has_no_runtime_imports_network_or_writes(tmp_path):
    site_path, manifest_path = write_plan_inputs(tmp_path)
    for filename in (".env", ".env.backend", "inventory.yml", "not-read.bundle"):
        (tmp_path / filename).write_text(PRIVATE_MARKER, encoding="utf-8")
    script = textwrap.dedent("""
        import os
        import sys

        def audit(event, arguments):
            if event.startswith("socket.") or event in {"subprocess.Popen", "os.system"}:
                raise AssertionError("Planning attempted networking or a command")
            if event in {"os.mkdir", "os.remove", "os.rename", "os.rmdir", "os.chmod", "os.symlink"}:
                raise AssertionError("Planning attempted a filesystem mutation")
            if event == "open":
                filename, mode, flags = arguments
                if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                    raise AssertionError("Planning attempted a file write")
                if isinstance(filename, str) and os.path.basename(filename) in {
                    ".env", ".env.backend", "inventory.yml", "not-read.bundle",
                }:
                    raise AssertionError("Planning attempted implicit input or bundle access")

        sys.addaudithook(audit)
        from tools.site_config.__main__ import main
        result = main(["plan", "--config", sys.argv[1], "--json"])
        forbidden = ("backend", "dotenv", "sqlalchemy", "psycopg", "redis", "paramiko", "requests")
        assert not any(name.split(".")[0] in forbidden for name in sys.modules)
        raise SystemExit(result)
    """)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(site_path)],
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
    assert str(manifest_path) not in result.stdout


def test_manifest_parser_rejects_duplicate_keys_at_any_depth():
    text = '{"manifest_version": 1, "components": [{"name": "agent-code", "name": "host-resources"}]}'
    with pytest.raises(ConfigValidationError) as caught:
        parse_release_manifest(text)
    assert {check.code for check in caught.value.checks} == {"manifest_syntax"}


def test_config_rejection_still_reports_release_blocked(tmp_path):
    data = copy.deepcopy(site_fixture(str(tmp_path / "manifest.json")))
    data["platform"]["cpu_arch"] = None
    (tmp_path / "manifest.json").write_text(json.dumps(manifest_fixture()), encoding="utf-8")
    site_path = tmp_path / "site.yaml"
    site_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    report = plan_site_report(site_path)
    assert report["status"] == "FAIL"
    blocked = [check for check in report["checks"] if check["check_id"] == "release.compatibility"]
    assert blocked and blocked[0]["status"] == "BLOCKED"
