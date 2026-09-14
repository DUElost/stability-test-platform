from __future__ import annotations

import fcntl
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from backend.agent.artifact_digest import collect_artifact_entries, digest_entries
from tools.site_config import stages
from tools.site_config.install import run_install
from tools.site_config.ops import CommandResult

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"
CODE_HEAD = "cafe1234"


class FakeOps:
    def __init__(
        self,
        *,
        hostname: str,
        mounts: set[str],
        users: set[str] | None = None,
        commands: set[str] | None = None,
        responses: dict[str, tuple[int, str]] | None = None,
    ):
        self.calls: list[tuple[str, ...]] = []
        self.envs: list[tuple[tuple[str, ...], dict | None]] = []
        self._hostname = hostname
        self._mounts = mounts
        self._users = set(users or ())
        self._commands = set(commands or ("python3", "systemctl", "nginx"))
        self._responses = responses or {}

    def run(self, argv, *, env=None, input_text=None, cwd=None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        self.envs.append((argv, env))
        if len(argv) >= 2 and argv[1] == "-c":
            result = subprocess.run(list(argv), env=env, cwd=cwd, capture_output=True, text=True, check=False)
            return CommandResult(argv, result.returncode, result.stdout)
        for needle, (code, output) in self._responses.items():
            if needle in " ".join(argv):
                return CommandResult(argv, code, output)
        if "-m" in argv and "alembic" in argv and "heads" in argv:
            return CommandResult(argv, 0, f"{CODE_HEAD} (head)\n")
        if "-m" in argv and "venv" in argv:
            target = Path(argv[-1]) / "bin"
            target.mkdir(parents=True, exist_ok=True)
            python = target / "python"
            python.write_text("#!/bin/sh\n", encoding="utf-8")
            os.chmod(python, 0o755)
            return CommandResult(argv, 0, "")
        if "bootstrap_admin.py" in " ".join(argv):
            return CommandResult(argv, 0, json.dumps({"status": "created"}))
        return CommandResult(argv, 0, "")

    def hostname(self) -> str:
        return self._hostname

    def local_addresses(self) -> set[str]:
        return {"127.0.0.1", self._hostname}

    def os_release(self) -> dict[str, str]:
        return {"ID": "debian", "VERSION_ID": "13"}

    def machine(self) -> str:
        return "x86_64"

    def user_exists(self, name: str) -> bool:
        return name in self._users

    def create_user(self, name: str, home: str) -> None:
        self._users.add(name)
        self.calls.append(("create_user", name))

    def ensure_dir(self, path: Path, mode: int, owner: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)

    def chown(self, path: Path, owner: str) -> None:
        self.calls.append(("chown", str(path), owner))

    def path_uid(self, path: Path) -> int | None:
        try:
            return os.lstat(path).st_uid
        except OSError:
            return None

    def is_mount(self, path: Path) -> bool:
        return str(path) in self._mounts

    def command_exists(self, name: str) -> bool:
        return name in self._commands


def _bundle(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    bundle = tmp_path / "bundle"
    (bundle / "backend/agent").mkdir(parents=True)
    (bundle / "backend/agent/sample.py").write_text("VALUE = 1\n", encoding="utf-8")
    (bundle / "backend/schemas").mkdir(parents=True)
    (bundle / "backend/schemas/pipeline_schema.json").write_text("{}\n", encoding="utf-8")
    (bundle / "backend/scripts").mkdir(parents=True)
    (bundle / "backend/scripts/bootstrap_admin.py").write_text("# stub\n", encoding="utf-8")
    (bundle / "frontend/dist-prod").mkdir(parents=True)
    (bundle / "frontend/dist-prod/index.html").write_text("<html></html>\n", encoding="utf-8")
    (bundle / "tools").mkdir()
    shutil.copytree(REPO_ROOT / "deploy/control-plane", bundle / "deploy/control-plane")
    extra = {"stp_schemas/pipeline_schema.json": str(bundle / "backend/schemas/pipeline_schema.json")}
    digests = {
        "agent-code": digest_entries(collect_artifact_entries(str(bundle / "backend/agent"), extra, kind="code")),
        "host-resources": digest_entries(collect_artifact_entries(str(bundle / "backend/agent"), extra, kind="resources")),
    }
    return bundle, digests


def _manifest(version: str, digests: dict[str, str]) -> dict:
    return {
        "manifest_version": 1,
        "product": {"version": version},
        "source": {"revision": "0123456789abcdef0123456789abcdef01234567"},
        "components": [
            {"name": "agent-code", "digest": digests["agent-code"]},
            {"name": "host-resources", "digest": digests["host-resources"]},
        ],
        "database": {"schema_target": CODE_HEAD},
        "compatibility": {
            "agent_protocol": ">=1.0,<2.0",
            "platforms": [
                {"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]},
                {"distribution": "ubuntu", "versions": ["24.04"], "cpu_arch": ["x86_64"]},
            ],
        },
        "provenance": {"attestation": "controlled_channel", "evidence_ref": "site_release_channel"},
    }


def _site(tmp_path: Path, bundle: Path, *, target: str, site_id: str, marker_display: str = "合成站点 I3") -> dict:
    deploy_root = tmp_path / "opt/stp-control"
    return {
        "schema_version": 1,
        "site": {"id": site_id, "display_name": marker_display, "timezone": "Asia/Shanghai"},
        "platform": {"os_family": "linux", "cpu_arch": "x86_64", "service_manager": "systemd"},
        "network": {"dependency_mode": "controlled_mirror"},
        "control_plane": {
            "target": target,
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "control_ssh",
            "deploy_root": str(deploy_root),
            "deploy_user": "stp",
            "public_url": f"http://{target}",
            "security_profile": "internal",
            "tls_ref": None,
        },
        "storage": {
            "provisioning": "existing_share",
            "protocol": "nfs",
            "target": "storage-i3.synthetic.invalid",
            "os": None,
            "ssh_user": None,
            "ssh_credential_ref": None,
            "share": "/srv/stp-export",
            "credential_ref": None,
            "mount_path": str(tmp_path / "mnt/share"),
        },
        "agents": [{
            "key": "agent-i3-01",
            "target": "agent-i3.synthetic.invalid",
            "os": {"distribution": "ubuntu", "version": "24.04"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "agent_ssh",
            "install_root": str(tmp_path / "opt/stp-agent"),
            "local_aee_root": str(tmp_path / "var/stp-aee"),
        }],
        "dependencies": {"database_ref": "site_database", "redis_ref": "site_redis", "tools_profile": "synthetic_tools"},
        "security": {
            "jwt_key_ref": "site_jwt",
            "agent_secret_ref": "site_agent_secret",
            "ssh_encryption_key_ref": "site_ssh_encryption",
            "initial_admin_ref": "site_admin",
        },
        "release": {
            "bundle": str(bundle),
            "manifest": str(bundle / "release-manifest.json"),
            "expected_release": "synthetic-2026.09.0",
        },
        "navigation": {"contact": "synthetic-ops", "documentation_url": "https://docs.synthetic.invalid/ops"},
    }


def prepare(tmp_path: Path, *, version: str = "synthetic-2026.09.0", tamper: bool = False):
    target, site_id = "control-i3.synthetic.invalid", "synthetic-i3"
    bundle, digests = _bundle(tmp_path)
    manifest = _manifest(version, digests)
    (bundle / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tamper:
        (bundle / "backend/agent/sample.py").write_text("VALUE = 2\n", encoding="utf-8")
    data = _site(tmp_path, bundle, target=target, site_id=site_id)
    config_path = tmp_path / "site.yaml"
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o700)
    (bindings / "site_database").write_text("DATABASE_URL=postgresql+asyncpg://stp:x@localhost:5432/stp\n", encoding="utf-8")
    (bindings / "site_redis").write_text("REDIS_URL=redis://localhost:6379/0\n", encoding="utf-8")
    (bindings / "site_admin").write_text(f"USERNAME=admin\nPASSWORD={PRIVATE_MARKER}\n", encoding="utf-8")
    for name in ("site_database", "site_redis", "site_admin"):
        os.chmod(bindings / name, 0o600)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    return config_path, bindings, state_dir, target, site_id, data


def ops_for(tmp_path: Path, *, responses=None) -> FakeOps:
    return FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        responses=responses,
    )


def invoke(tmp_path, *, dry_run=False, ops=None, probe=None, confirm_target="control-i3.synthetic.invalid",
           config_path=None, bindings=None, state_dir=None):
    config_path = config_path or tmp_path / "site.yaml"
    bindings = bindings or tmp_path / "bindings"
    state_dir = state_dir or tmp_path / "state"
    return run_install(
        config_path,
        bindings_dir=bindings,
        state_dir=state_dir,
        confirm_site="synthetic-i3",
        confirm_target=confirm_target,
        dry_run=dry_run,
        ops=ops or ops_for(tmp_path),
        db_probe=probe or (lambda dsn: ("empty", None)),
        system_root=tmp_path / "system",
    )


def codes(report: dict) -> set[str]:
    return {check["code"] for check in report["checks"]}


def test_dry_run_verifies_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path, dry_run=True)
    assert report["status"] == "PASS"
    assert not (tmp_path / "opt/stp-control/.stp-site.json").exists()
    assert not (tmp_path / "state/install-state.json").exists()
    assert not (tmp_path / "system/etc").exists()


def test_wrong_confirm_target_is_rejected_before_writes(tmp_path):
    prepare(tmp_path)
    report = invoke(tmp_path, confirm_target="other.synthetic.invalid")
    assert report["status"] == "FAIL"
    assert "install_confirm" in codes(report)
    assert not (tmp_path / "state/install-state.json").exists()


def test_wrong_hostname_evidence_is_rejected(tmp_path):
    prepare(tmp_path)
    ops = FakeOps(hostname="someone-else", mounts={str(tmp_path / "mnt/share")})
    report = invoke(tmp_path, ops=ops)
    assert "install_hostname" in codes(report)


def test_foreign_marker_blocks_install(tmp_path):
    prepare(tmp_path)
    root = tmp_path / "opt/stp-control"
    root.mkdir(parents=True, exist_ok=True)
    (root / ".stp-site.json").write_text(json.dumps({"site_id": "other-site"}), encoding="utf-8")
    report = invoke(tmp_path)
    assert "install_root_taken" in codes(report)


def test_tampered_bundle_digest_blocks_install(tmp_path):
    prepare(tmp_path, tamper=True)
    report = invoke(tmp_path)
    assert "release_digest" in codes(report)


def test_full_install_is_idempotent_and_keeps_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    first_ops = ops_for(tmp_path)
    first = invoke(tmp_path, ops=first_ops)
    assert first["status"] == "PASS", first
    marker = tmp_path / "opt/stp-control/.stp-site.json"
    assert marker.is_file()
    assert (tmp_path / "state/install-state.json").is_file()
    env_file = tmp_path / "opt/stp-control/.env.backend"
    env_bytes = env_file.read_bytes()
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    bootstrap_env = next(env for argv, env in first_ops.envs if "bootstrap_admin.py" in " ".join(argv))
    assert bootstrap_env and "JWT_SECRET_KEY" in bootstrap_env
    assert "STP_INITIAL_ADMIN_USER" in bootstrap_env
    assert ("chown", str(tmp_path / "opt/stp-control"), "stp") in first_ops.calls

    second_ops = ops_for(tmp_path)
    second = invoke(tmp_path, ops=second_ops, probe=lambda dsn: ("managed", CODE_HEAD))
    assert second["status"] == "PASS", second
    assert env_file.read_bytes() == env_bytes
    joined = " ".join(" ".join(call) for call in second_ops.calls)
    assert "upgrade head" not in joined


def test_unmanaged_database_blocks_before_migration(tmp_path):
    prepare(tmp_path)
    report = invoke(tmp_path, probe=lambda dsn: ("unmanaged", None))
    assert "db_unmanaged" in codes(report)
    assert not (tmp_path / "system/etc").exists()


def test_migration_failure_stops_before_service_start(tmp_path):
    prepare(tmp_path)
    ops = ops_for(tmp_path, responses={"alembic upgrade": (1, "")})
    report = invoke(tmp_path, ops=ops)
    assert "db_migrate_failed" in codes(report)
    joined = " ".join(" ".join(call) for call in ops.calls)
    assert "enable" not in joined
    assert not (tmp_path / "system/etc").exists()


def test_partial_install_can_resume_after_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    ops_without_mount = FakeOps(hostname="control-i3.synthetic.invalid", mounts=set())
    first = invoke(tmp_path, ops=ops_without_mount)
    assert "install_storage" in codes(first)
    assert (tmp_path / "opt/stp-control/.stp-site.json").is_file()
    second = invoke(tmp_path)
    assert second["status"] == "PASS", second


def test_locked_state_dir_refuses_concurrent_run(tmp_path):
    prepare(tmp_path)
    lock_path = tmp_path / "state/install.lock"
    with open(lock_path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        report = invoke(tmp_path)
    assert "state_locked" in codes(report)


def test_insecure_bindings_are_rejected(tmp_path):
    prepare(tmp_path)
    os.chmod(tmp_path / "bindings", 0o755)
    report = invoke(tmp_path)
    assert "binding_dir" in codes(report)
    os.chmod(tmp_path / "bindings", 0o700)
    os.chmod(tmp_path / "bindings/site_redis", 0o644)
    report = invoke(tmp_path)
    assert "binding_file" in codes(report)
    assert PRIVATE_MARKER not in json.dumps(report)


def test_report_and_state_never_echo_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    config_path, bindings, state_dir, target, site_id, data = prepare(tmp_path)
    data["site"]["display_name"] = PRIVATE_MARKER
    data["storage"]["share"] = f"/srv/{PRIVATE_MARKER.lower()}"
    config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    report = invoke(tmp_path)
    assert report["status"] == "PASS", report
    blob = json.dumps(report, ensure_ascii=False) + (state_dir / "install-state.json").read_text(encoding="utf-8")
    assert PRIVATE_MARKER not in blob
    assert str(tmp_path) not in json.dumps(report, ensure_ascii=False)


def test_bootstrap_admin_rejects_invalid_input_without_runtime():
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(REPO_ROOT),
        "PYTHONDONTWRITEBYTECODE": "1",
        "STP_INITIAL_ADMIN_USER": "admin",
        "STP_INITIAL_ADMIN_PASSWORD": "short",
    }
    result = subprocess.run(
        [sys.executable, "-B", str(REPO_ROOT / "backend/scripts/bootstrap_admin.py")],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "invalid"
    assert PRIVATE_MARKER not in result.stdout + result.stderr
