from __future__ import annotations

import base64
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
# 合法 Fernet 键（32 字节 urlsafe-base64）——S0 只校验形状，不做加密运算
FERNET_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()


class FakeOps:
    def __init__(
        self,
        *,
        hostname: str,
        mounts: set[str],
        users: set[str] | None = None,
        commands: set[str] | None = None,
        responses: dict[str, tuple[int, str]] | None = None,
        timezone: str = "Asia/Shanghai",
    ):
        self._timezone = timezone
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

    def timezone(self) -> str:
        return self._timezone

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
    # 与仓库同形：payload 目录里存在指向同目录文档的符号链接（harness 约定）。
    # 落地不得把它实体化，否则部署摘要与清单基准不一致（I4 实验室实测）。
    (bundle / "backend/agent/AGENTS.md").write_text("# agent contract\n", encoding="utf-8")
    os.symlink("AGENTS.md", bundle / "backend/agent/CLAUDE.md")
    (bundle / "backend/schemas").mkdir(parents=True)
    (bundle / "backend/schemas/pipeline_schema.json").write_text("{}\n", encoding="utf-8")
    (bundle / "backend/scripts").mkdir(parents=True)
    (bundle / "backend/scripts/bootstrap_admin.py").write_text("# stub\n", encoding="utf-8")
    (bundle / "frontend/dist-prod").mkdir(parents=True)
    (bundle / "frontend/dist-prod/index.html").write_text("<html></html>\n", encoding="utf-8")
    (bundle / "tools").mkdir()
    shutil.copytree(REPO_ROOT / "deploy/control-plane", bundle / "deploy/control-plane")
    # 监控栈模板与告警规则同目录（发布物含整个 deploy/）
    shutil.copytree(REPO_ROOT / "deploy/prometheus", bundle / "deploy/prometheus")
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
    (bindings / "site_ssh_encryption").write_text(
        f"SSH_CREDENTIALS_FERNET_KEY={FERNET_KEY}\n", encoding="utf-8",
    )
    for name in ("site_database", "site_redis", "site_admin", "site_ssh_encryption"):
        os.chmod(bindings / name, 0o600)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)
    return config_path, bindings, state_dir, target, site_id, data


_PACKAGE_BINARIES = ("prometheus", "prometheus-node-exporter", "exportfs")


class InstallingFakeOps(FakeOps):
    """apt-get install 之后命令就可用（真实 dpkg 的效果）。

    安装链现在以「可执行文件在不在」为判据，并要求装完仍在才算 FAIL——
    没有这个模拟，正常用例会在「装完仍缺」那一关被误判。
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.installed: set[str] = set()

    def run(self, argv, *, env=None, input_text=None, cwd=None):
        result = super().run(argv, env=env, input_text=input_text, cwd=cwd)
        if " ".join(str(item) for item in argv).startswith("apt-get install"):
            self.installed.update(_PACKAGE_BINARIES)
        return result

    def command_exists(self, name: str) -> bool:
        return name in self.installed or super().command_exists(name)


def ops_for(tmp_path: Path, *, responses=None) -> FakeOps:
    return FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        responses=responses,
    )


def invoke(tmp_path, *, dry_run=False, ops=None, probe=None, confirm_target="control-i3.synthetic.invalid",
           config_path=None, bindings=None, state_dir=None, agents_inventory=None):
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
        agents_inventory=agents_inventory,
        ops=ops or ops_for(tmp_path),
        db_probe=probe or (lambda dsn: ("empty", None)),
        system_root=tmp_path / "system",
    )


@pytest.fixture(autouse=True)
def _stub_entry_probe(monkeypatch):
    """S4 会探测站点入口（`/` 200）；单测不联网，统一 stub 为通过。"""
    monkeypatch.setattr(stages, "await_frontend", lambda *a, **k: True)


def env_line(text: str, key: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{key} not in env")


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
    env_text = env_bytes.decode(encoding="utf-8")
    # I4：S2 按 public_url 渲染 Agent 安装回连地址（Agent 端 .env 的 API_URL 来源）
    assert "STP_AGENT_INSTALL_API_URL=http://control-i3.synthetic.invalid" in env_text
    # I5：S2 渲染站点导航页到 nginx 可读目录（只发布获准信息）
    nav = tmp_path / "system/var/www/stability-site/index.html"
    assert nav.is_file()
    assert stat.S_IMODE(nav.stat().st_mode) == 0o644
    nav_text = nav.read_text(encoding="utf-8")
    assert "synthetic-i3" in nav_text and "synthetic-ops" in nav_text
    assert "handover.json" in nav_text
    assert "<deploy-root>" not in nav_text
    # I4 修复：站点级秘密必须首次生成（不得把模板占位值带上线），
    # 且 SSH 口令加密键来自受保护绑定（留空会让密码型 Host 创建 503）。
    for key in ("JWT_SECRET_KEY", "AGENT_SECRET", "WS_TOKEN"):
        value = env_line(env_text, key)
        assert "change-me" not in value and len(value) >= 32, (key, value)
    assert env_line(env_text, "SSH_CREDENTIALS_FERNET_KEY") == FERNET_KEY
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


def test_landed_tree_keeps_symlinks(tmp_path, monkeypatch):
    """S2 落地必须原样保留符号链接（copytree 默认解引用会改变内容摘要）。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path, probe=lambda dsn: ("empty", None))
    assert report["status"] == "PASS", report["checks"]
    landed = tmp_path / "opt/stp-control/backend/agent/CLAUDE.md"
    assert landed.is_symlink(), "落地树把符号链接实体化了"
    assert os.readlink(landed) == "AGENTS.md"
    # 重跑：源链接与已存在的链接共存，落地不得失败
    second = invoke(tmp_path, ops=ops_for(tmp_path), probe=lambda dsn: ("empty", None))
    assert second["status"] == "PASS", second["checks"]
    assert landed.is_symlink()


def test_entry_without_frontend_is_reported(tmp_path, monkeypatch):
    """入口不能服务前端（部署根不可穿越/root 写错）必须 FAIL，而不是只看 /health。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    monkeypatch.setattr(stages, "await_frontend", lambda *a, **k: False)
    prepare(tmp_path)
    report = invoke(tmp_path)

    assert "install_frontend" in codes(report)


def test_missing_ssh_encryption_binding_blocks_install(tmp_path):
    """SSH 口令加密键缺失即 S0 FAIL——否则站点装好后密码型 Host 创建 503。"""
    prepare(tmp_path)
    (tmp_path / "bindings/site_ssh_encryption").unlink()
    report = invoke(tmp_path)
    assert "binding_file" in codes(report)
    assert report["stages"] == []


def test_invalid_ssh_encryption_key_blocks_install(tmp_path):
    """Fernet 键形状不合法即阻断（不做加密运算也能在 S0 判形状）。"""
    prepare(tmp_path)
    path = tmp_path / "bindings/site_ssh_encryption"
    path.write_text("SSH_CREDENTIALS_FERNET_KEY=not-a-fernet-key\n", encoding="utf-8")
    os.chmod(path, 0o600)
    report = invoke(tmp_path)
    assert "binding_content" in codes(report)
    assert report["stages"] == []


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


def _inventory(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hosts.ini"
    path.write_text(text, encoding="utf-8")
    return path


def test_agents_inventory_is_merged_and_its_bindings_materialized(tmp_path):
    """inventory 的共享凭据必须落到绑定目录，S5 才能解析 ssh_credential_ref。"""
    prepare(tmp_path)
    # 与已声明 Agent 同安装根：站点级单值（异构根会被模型拒绝）
    inventory = _inventory(tmp_path, (
        "[stp_agents]\n"
        f"10.99.0.21 ansible_user=ops ansible_password={PRIVATE_MARKER}"
        f" install_root={tmp_path / 'opt/stp-agent'}\n"
    ))
    report = invoke(tmp_path, agents_inventory=inventory, dry_run=True)
    assert report["status"] == "PASS"
    # dry-run 只报名字，不写凭据
    assert report["materialized_bindings"] == ["agent_ssh"]
    assert not (tmp_path / "bindings/agent_ssh").exists()
    assert PRIVATE_MARKER not in json.dumps(report, ensure_ascii=False)


def test_agents_inventory_writes_the_shared_credential_owner_only(tmp_path, monkeypatch):
    prepare(tmp_path)
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    inventory = _inventory(tmp_path, (
        "[stp_agents]\n"
        f"10.99.0.21 ansible_user=ops ansible_password={PRIVATE_MARKER}"
        f" install_root={tmp_path / 'opt/stp-agent'}\n"
    ))
    report = invoke(tmp_path, agents_inventory=inventory)
    assert report["status"] == "PASS"
    path = tmp_path / "bindings/agent_ssh"
    assert stat.S_IMODE(os.lstat(path).st_mode) == 0o600
    assert f"PASSWORD={PRIVATE_MARKER}" in path.read_text(encoding="utf-8")
    assert PRIVATE_MARKER not in json.dumps(report, ensure_ascii=False)


def test_bad_inventory_line_fails_closed_with_the_offending_host(tmp_path):
    prepare(tmp_path)
    inventory = _inventory(tmp_path, f"[stp_agents]\n10.99.0.21 ansible_password={PRIVATE_MARKER}\n")
    report = invoke(tmp_path, agents_inventory=inventory)
    assert report["status"] == "FAIL"
    check = next(item for item in report["checks"] if item["check_id"] == "install.inventory")
    assert check["code"] == "inventory_user_missing"
    assert "10.99.0.21" in check["message"]
    assert PRIVATE_MARKER not in json.dumps(report, ensure_ascii=False)
    assert not (tmp_path / "bindings/agent_ssh").exists()


def test_inventory_that_breaks_a_site_invariant_is_a_configuration_failure(tmp_path):
    """异构安装根：inventory 不能绕过站点约束，且必须是检查项而不是异常。"""
    prepare(tmp_path)
    inventory = _inventory(tmp_path, (
        "[stp_agents]\n10.99.0.21 ansible_user=ops ansible_password=x install_root=/opt/other-root\n"
    ))
    report = invoke(tmp_path, agents_inventory=inventory)
    assert report["status"] == "FAIL"
    assert "agent_install_root_mismatch" in codes(report)


def test_install_without_inventory_does_not_touch_agent_bindings(tmp_path, monkeypatch):
    prepare(tmp_path)
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    report = invoke(tmp_path)
    assert report["status"] == "PASS"
    assert "materialized_bindings" not in report
    assert not (tmp_path / "bindings/agent_ssh").exists()


def test_foreign_shared_system_paths_block_s4_without_writes(tmp_path, monkeypatch):
    """#2088：同名 unit/nginx 属于别的站点时 S4 阻断，且不落任何共享路径写入。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    unit = tmp_path / "system/etc/systemd/system/stability-backend-nomigrate.service"
    unit.parent.mkdir(parents=True)
    foreign = "[Unit]\nDescription=other site\n[Service]\nWorkingDirectory=/opt/other-site\n"
    unit.write_text(foreign, encoding="utf-8")
    report = invoke(tmp_path)
    check = next(item for item in report["checks"] if item["check_id"] == "install.s4.shared_paths")
    assert check["code"] == "install_conflict"
    assert unit.read_text(encoding="utf-8") == foreign
    assert not (tmp_path / "system/etc/nginx").exists()
    assert not (tmp_path / "system/etc/logrotate.d").exists()


def test_shared_system_paths_rerun_leaves_rollback_copy(tmp_path, monkeypatch):
    """本站重跑允许覆盖，但旧内容必须留下可回放的副本（不写进系统路径）。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    assert invoke(tmp_path)["status"] == "PASS"
    unit = tmp_path / "system/etc/systemd/system/stability-backend-nomigrate.service"
    assert "WorkingDirectory=" in unit.read_text(encoding="utf-8")
    second = invoke(tmp_path, probe=lambda dsn: ("managed", CODE_HEAD))
    assert second["status"] == "PASS", second["checks"]
    backup = tmp_path / "state/shared-path-prev/stability-backend-nomigrate.service"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == unit.read_text(encoding="utf-8")


def test_distribution_default_site_is_disabled_not_deleted(tmp_path, monkeypatch):
    """#2088：发行版默认站点是他人资产——移出 sites-enabled 停用，不静默删除。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    enabled = tmp_path / "system/etc/nginx/sites-enabled"
    enabled.mkdir(parents=True)
    (enabled / "default").symlink_to("../sites-available/default")
    report = invoke(tmp_path)
    assert report["status"] == "PASS", report["checks"]
    assert not (enabled / "default").is_symlink()
    stash = tmp_path / "system/etc/nginx/sites-available/stp-disabled-default"
    assert stash.is_symlink() and os.readlink(stash) == "../sites-available/default"


_LYING_DIGEST_STUB = '''
import json
import os

_STATE = {"index": -1}


def collect_artifact_entries(*args, **kwargs):
    return []


def digest_entries(entries):
    root = os.environ.get("STP_INSTALL_BUNDLE") or os.getcwd()
    with open(os.path.join(root, "release-manifest.json"), encoding="utf-8") as handle:
        declared = [component["digest"] for component in json.load(handle)["components"]]
    _STATE["index"] += 1
    return declared[_STATE["index"]]
'''


def test_bundle_cannot_supply_its_own_digest_implementation(tmp_path, monkeypatch):
    """#2020：量具不受被测物控制。

    攻击形态——被测树里放一份「回显清单声明值」的 digest 实现，并让该树成为解释器
    的 ``sys.path[0]``。旧实现以 ``PYTHONPATH=ctx.bundle`` 起子进程 import 它，S0 会对
    任意内容的 bundle 放行；现在 digest 只取安装器自身源码树的受信副本。
    """
    _config, _bindings, _state, _target, _site, data = prepare(tmp_path)
    bundle = Path(data["release"]["bundle"])
    (bundle / "backend/agent/artifact_digest.py").write_text(_LYING_DIGEST_STUB, encoding="utf-8")
    monkeypatch.chdir(bundle)
    ops = ops_for(tmp_path)
    report = invoke(tmp_path, ops=ops)
    assert "release_digest" in codes(report)
    assert not any(len(argv) > 1 and argv[1] == "-c" for argv in ops.calls)


def test_unresolved_placeholder_in_env_template_blocks_s2(tmp_path, monkeypatch):
    """#2017 残口：模板键与受管键失配时，占位符不得原样落进 .env.backend 且仍报 PASS。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    _config, _bindings, _state, _target, _site, data = prepare(tmp_path)
    template = Path(data["release"]["bundle"]) / "deploy/control-plane/env/.env.backend.internal.example"
    template.write_text(template.read_text(encoding="utf-8") + "\nSTP_FUTURE_KEY=<deploy-root>\n", encoding="utf-8")
    report = invoke(tmp_path)
    assert "install_conflict" in codes(report)
    assert not (tmp_path / "opt/stp-control/.env.backend").exists()
def test_effective_env_keys_ignores_comments_and_blank_lines():
    """模板注释不是键：systemd 不读它，检查也不能当它存在。"""
    text = "# STP_SCRIPT_RUNTIME_ROOT=/x\n\nSTP_SCRIPT_ROOT=/y\n   # indented comment\nnot a key\n"
    assert stages._effective_env_keys(text) == {"STP_SCRIPT_ROOT"}


def test_first_agent_onboarding_renders_the_script_runtime_key(tmp_path, monkeypatch):
    """首装无 Agent → 该键保持注释；首台 Agent 接入 → 必须补成有效行。

    否则后端读不到脚本同步配置，S6 受控链的准入会以
    `script_sync_config_error` 失败（238 现场实测）。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    data = yaml.safe_load((tmp_path / "site.yaml").read_text(encoding="utf-8"))
    data["agents"] = []
    empty_site = tmp_path / "site-empty.yaml"
    empty_site.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    first = invoke(tmp_path, config_path=empty_site)
    assert first["status"] == "PASS"
    env = tmp_path / "opt/stp-control/.env.backend"
    lines = env.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("# STP_SCRIPT_RUNTIME_ROOT=") for line in lines)
    assert not any(line.startswith("STP_SCRIPT_RUNTIME_ROOT=") for line in lines)
    before = env.read_text(encoding="utf-8")

    inventory = tmp_path / "hosts.ini"
    inventory.write_text(
        f"[stp_agents]\n10.99.0.31 ansible_user=ops ansible_password=secret"
        f" install_root={tmp_path / 'opt/stp-agent'}\n",
        encoding="utf-8",
    )
    second = invoke(tmp_path, config_path=empty_site, agents_inventory=inventory)
    assert second["status"] == "PASS"
    after = env.read_text(encoding="utf-8")
    assert after.startswith(before), "既有值必须原样保留（只追加缺失键）"
    effective = stages._effective_env_keys(after)
    assert "STP_SCRIPT_RUNTIME_ROOT" in effective
    assert str(tmp_path / "opt/stp-agent/agent/scripts") in after
    assert "env_extended" in {check["code"] for check in second["checks"]}


def test_missing_secret_bearing_env_key_still_conflicts(tmp_path, monkeypatch):
    """缺的是别的重要键（例如秘密）→ 仍然 fail-closed，不静默补。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path)
    assert report["status"] == "PASS"
    env = tmp_path / "opt/stp-control/.env.backend"
    env.write_text(
        "\n".join(
            line for line in env.read_text(encoding="utf-8").splitlines()
            if not line.startswith("JWT_SECRET_KEY=")
        ) + "\n",
        encoding="utf-8",
    )
    second = invoke(tmp_path)
    assert second["status"] == "FAIL"
    assert "install_conflict" in codes(second)


# ── #2197 站点本地监控栈（/storage 页的数据源）────────────────────────────


DISTRO_UNITS = {
    "prometheus.service": "[Unit]\nDescription=Prometheus\n\n[Service]\nEnvironmentFile=-/etc/default/prometheus\nExecStart=/usr/bin/prometheus $ARGS\n",
    "prometheus-node-exporter.service": "[Unit]\nDescription=Node Exporter\n\n[Service]\nEnvironmentFile=-/etc/default/prometheus-node-exporter\nExecStart=/usr/bin/prometheus-node-exporter $ARGS\n",
}


def _monitoring_site(tmp_path: Path, *, enabled: bool = True, port: int = 9091) -> Path:
    path = tmp_path / "site-monitoring.yaml"
    data = yaml.safe_load((tmp_path / "site.yaml").read_text(encoding="utf-8"))
    data["monitoring"] = {"enabled": enabled, "prometheus_port": port}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _distro_units(tmp_path: Path, units: dict[str, str] | None = None) -> None:
    directory = tmp_path / "system/usr/lib/systemd/system"
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in (DISTRO_UNITS if units is None else units).items():
        (directory / name).write_text(text, encoding="utf-8")


def _system_file(tmp_path: Path, relative: str) -> Path:
    return tmp_path / "system" / relative


@pytest.fixture(autouse=True)
def _stub_monitoring_probe(monkeypatch):
    """S4 会探 Prometheus 的 /-/ready；单测不联网，统一 stub 为就绪。"""
    monkeypatch.setattr(stages, "await_monitoring", lambda *a, **k: True)


def test_monitoring_stack_is_installed_and_enabled(tmp_path, monkeypatch):
    """包装上、配置落地、三个单元启用、/-/ready 通过——页面才有数据源。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path), ops=ops)

    assert report["status"] == "PASS", report
    assert "monitoring_installed" in codes(report)
    assert "monitoring_ready" in codes(report)
    joined = [" ".join(call) for call in ops.calls]
    assert any("apt-get install -y prometheus prometheus-node-exporter" in call for call in joined)
    for unit in ("prometheus-node-exporter", "prometheus", "stp-mem-top.timer"):
        assert f"systemctl enable {unit}" in joined, unit
        # 发行版包在 apt 阶段已把服务按默认参数拉起：不 restart 的话 $ARGS 永远不生效
        assert f"systemctl restart {unit}" in joined, unit
    # 页面读的两个采集面：NFS 服务端指标与宿主进程内存采样器
    assert _system_file(tmp_path, "etc/default/prometheus-node-exporter").read_text(
        encoding="utf-8"
    ).find("--collector.nfsd") != -1
    assert (_system_file(tmp_path, "var/lib/prometheus/node-exporter")).is_dir()
    assert _system_file(tmp_path, "usr/local/sbin/stp-mem-top").stat().st_mode & 0o777 == 0o755


def test_monitoring_config_matches_the_backend_defaults(tmp_path, monkeypatch):
    """抓取配置必须落在后端默认查询面：job file-server + 回环 9091/9100。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path, port=9091), ops=ops)

    assert report["status"] == "PASS", report
    scrape = _system_file(tmp_path, "etc/stp/prometheus/prometheus.yml").read_text(encoding="utf-8")
    assert "job_name: file-server" in scrape
    assert "127.0.0.1:9100" in scrape
    assert "<" not in scrape, "渲染后仍有未替换占位符"
    args = _system_file(tmp_path, "etc/default/prometheus").read_text(encoding="utf-8")
    assert "--web.listen-address=127.0.0.1:9091" in args
    assert "--config.file=/etc/stp/prometheus/prometheus.yml" in args


def test_custom_port_is_rendered(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path, port=9191), ops=ops)

    assert report["status"] == "PASS", report
    assert "--web.listen-address=127.0.0.1:9191" in _system_file(
        tmp_path, "etc/default/prometheus"
    ).read_text(encoding="utf-8")


def test_distro_unit_without_args_support_fails_before_writing(tmp_path, monkeypatch):
    """发行版 unit 不读 $ARGS 时不得写 /etc/default：启动参数会被静默忽略。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path, {"prometheus.service": "[Service]\nExecStart=/usr/bin/prometheus\n"})

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path))

    assert report["status"] == "FAIL"
    assert "install_monitoring" in codes(report)
    assert not _system_file(tmp_path, "etc/default/prometheus").exists()


def test_unready_prometheus_fails_instead_of_passing(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    monkeypatch.setattr(stages, "await_monitoring", lambda *a, **k: False)
    prepare(tmp_path)
    _distro_units(tmp_path)

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path))

    assert report["status"] == "FAIL"
    assert "install_monitoring" in codes(report)


def test_monitoring_disabled_touches_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    ops = ops_for(tmp_path)

    report = invoke(tmp_path, ops=ops)

    assert report["status"] == "PASS", report
    assert not any("prometheus" in " ".join(call) for call in ops.calls)
    assert not _system_file(tmp_path, "etc/default/prometheus").exists()
    assert "install.s4.monitoring" not in {check["check_id"] for check in report["checks"]}


def test_monitoring_dry_run_plans_without_writing(tmp_path):
    prepare(tmp_path)
    _distro_units(tmp_path)

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path), dry_run=True)

    assert "monitoring_planned" in codes(report)
    assert not _system_file(tmp_path, "etc/default/prometheus").exists()


# ── #2181 自建中心存储：控制面导出 ────────────────────────────────────────


def _exporting_site(tmp_path: Path, *, agents: list[dict] | None = None, name: str = "site-export.yaml") -> Path:
    """把合成站点改成自建导出（local_mount + export_to_agents）。"""
    path = tmp_path / name
    data = yaml.safe_load((tmp_path / "site.yaml").read_text(encoding="utf-8"))
    data["storage"] = {
        "provisioning": "local_mount",
        "protocol": None,
        "target": None,
        "os": None,
        "ssh_user": None,
        "ssh_credential_ref": None,
        "share": None,
        "credential_ref": None,
        "mount_path": data["storage"]["mount_path"],
        "export_to_agents": True,
    }
    if agents is not None:
        data["agents"] = agents
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _exports_file(tmp_path: Path, site_id: str = "synthetic-i3") -> Path:
    return tmp_path / "system" / "etc/exports.d" / f"stp-{site_id}.exports"


def _agent_config(tmp_path: Path, *, target: str) -> list[dict]:
    return [{
        "key": "agent-i3-01",
        "target": target,
        "os": {"distribution": "debian", "version": "13"},
        "ssh_user": "bootstrap",
        "ssh_credential_ref": "agent_ssh",
        "install_root": str(tmp_path / "opt/stp-agent"),
        "local_aee_root": str(tmp_path / "var/stp-aee"),
    }]


def test_export_client_scope_follows_the_declared_agent_targets(tmp_path):
    """客户端列表就是声明本身：IPv4 收敛到 /24，主机名只能放宽到所有客户端。"""
    from tools.site_config.stages import render_exports
    from tools.site_config.validation import load_site_config

    prepare(tmp_path)
    ipv4 = _exporting_site(
        tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"), name="site-ipv4.yaml",
    )
    hostname = _exporting_site(
        tmp_path, agents=_agent_config(tmp_path, target="agent-i3.synthetic.invalid"),
        name="site-hostname.yaml",
    )
    empty = _exporting_site(tmp_path, agents=[], name="site-empty.yaml")

    listed = render_exports(load_site_config(ipv4))
    widened = render_exports(load_site_config(hostname))
    deferred = render_exports(load_site_config(empty))

    assert "198.51.100.0/24(rw,sync,no_subtree_check,all_squash,anonuid=1000,anongid=1000)" in listed
    assert str(tmp_path / "mnt/share") in listed
    assert "*(" in widened and "/24" not in widened
    assert deferred == "", "没有 Agent 就没有客户端，不能凭空导出给所有人"


def test_install_publishes_the_export_to_the_declared_agents(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))
    ops = FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        commands={"python3", "systemctl", "nginx", "exportfs"},
    )

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "PASS", report
    exports = _exports_file(tmp_path)
    assert exports.read_text(encoding="utf-8").count("198.51.100.0/24(") == 1
    assert stat.S_IMODE(exports.stat().st_mode) == 0o644
    joined = [" ".join(call) for call in ops.calls]
    assert "exportfs -ra" in joined, "写了 exports 文件却没有重载导出"
    assert "systemctl enable --now nfs-server" in joined
    assert "chown root:1000 " + str(tmp_path / "mnt/share") in joined
    assert "chmod 0775 " + str(tmp_path / "mnt/share") in joined


def test_missing_nfs_server_package_is_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "PASS", report
    assert any("apt-get install -y nfs-kernel-server" in " ".join(call) for call in ops.calls)


def test_foreign_share_is_never_exported_by_this_site(tmp_path, monkeypatch):
    """外部分享站点不由本站导出：一个字节都不写，也不碰 NFS 服务端。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    ops = ops_for(tmp_path)

    report = invoke(tmp_path, ops=ops)

    assert report["status"] == "PASS", report
    assert not _exports_file(tmp_path).exists()
    assert not any("exportfs" in call for call in (" ".join(c) for c in ops.calls))
    assert "install.s2.export" not in {check["check_id"] for check in report["checks"]}


def test_failed_export_reload_fails_the_stage(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))
    ops = ops_for(tmp_path)
    ops._responses["exportfs"] = (1, "boom")

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "FAIL"
    assert "install_export" in codes(report)


def test_first_install_without_agents_defers_the_export(tmp_path, monkeypatch):
    """先装控制面、Agent 随后接入：如实标 export_deferred，不假装已经导出。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=[])
    ops = FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        commands={"python3", "systemctl", "nginx", "exportfs"},
    )

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "PASS", report
    assert "export_deferred" in codes(report)
    assert _exports_file(tmp_path).read_text(encoding="utf-8") == ""


def test_dry_run_plans_the_export_without_writing(tmp_path):
    """dry-run 不得声称已发布：报告写的是计划，磁盘上不能有文件。"""
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))

    report = invoke(tmp_path, config_path=config_path, dry_run=True)

    assert "export_planned" in codes(report)
    assert not _exports_file(tmp_path).exists()


# ── 238 现场缺陷：包/命令判据（#2181 / #2197）─────────────────────────────


def test_export_install_that_still_leaves_exportfs_missing_fails_closed(tmp_path, monkeypatch):
    """apt 报成功但命令还是没有（源里没有该包/被策略拦）→ FAIL，不带病进 S2。

    现场实景：`dpkg -l nfs-kernel-server` 对「已知但未安装」的包返回 0，
    安装被跳过，S2 调 exportfs 抛 FileNotFoundError 把整次安装崩成 traceback。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))
    ops = ops_for(tmp_path)  # 不模拟安装成功：exportfs 始终缺失
    ops._responses["apt-get"] = (0, "")

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "FAIL"
    assert "install_export" in codes(report)
    # 缺命令必须在 S1 就报出来，而不是等到 S2 崩
    assert not any(check["check_id"] == "install.s2.export" for check in report["checks"])


def test_export_skips_apt_when_the_command_is_already_there(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    config_path = _exporting_site(tmp_path, agents=_agent_config(tmp_path, target="198.51.100.7"))
    ops = FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        commands={"python3", "systemctl", "nginx", "exportfs"},
    )

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "PASS", report
    assert not any("apt-get" in " ".join(call) for call in ops.calls)


def test_monitoring_install_that_still_leaves_binaries_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    ops = ops_for(tmp_path)
    ops._responses["apt-get"] = (0, "")

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path), ops=ops)

    assert report["status"] == "FAIL"
    assert "install_monitoring" in codes(report)


def test_local_ops_timezone_falls_back_to_timedatectl(monkeypatch):
    """没有 /etc/timezone 的主机（238 现场即如此）必须走 timedatectl，而不是返回空串。

    空串会让 S1 的时区检查退化成 BLOCKED timezone_unknown——本该能判定的机器被判成「读不到」。
    """
    from tools.site_config.ops import CommandResult, LocalOps

    def _no_timezone_file(self, *args, **kwargs):
        raise FileNotFoundError("/etc/timezone")

    monkeypatch.setattr(Path, "read_text", _no_timezone_file)
    monkeypatch.setattr(
        LocalOps, "run",
        lambda self, argv, **kwargs: CommandResult(tuple(argv), 0, "Asia/Shanghai\n"),
    )

    assert LocalOps().timezone() == "Asia/Shanghai"


def test_local_ops_reports_a_missing_command_instead_of_raising():
    """命令不存在是一种结果（127），不是异常：否则一个缺失的外部命令能崩掉整次安装。"""
    from tools.site_config.ops import LocalOps

    result = LocalOps().run(["stp-command-that-does-not-exist"])

    assert result.returncode == 127
    assert "command not found" in result.stderr


# ── 238 现场缺陷二：/etc/default/* 是发行版资产，不适用本站标记守卫 ──────────


DISTRO_ARGS_FILE = 'ARGS=""  # distribution default\n'


def _seed_distro_default(tmp_path: Path, relative: str, text: str = DISTRO_ARGS_FILE) -> Path:
    path = tmp_path / "system" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_distro_defaults_do_not_block_the_monitoring_stack(tmp_path, monkeypatch):
    """发行版出厂的 /etc/default/prometheus 不带本站标记——用共享路径守卫会永远装不上。

    现场实景：238 的这两个文件是包自带的 `ARGS=""`，S4 直接 install_conflict FAIL；
    正确行为是「先备份再覆盖」，重跑幂等。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    prometheus_default = _seed_distro_default(tmp_path, "etc/default/prometheus")
    _seed_distro_default(tmp_path, "etc/default/prometheus-node-exporter")
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})
    config_path = _monitoring_site(tmp_path)

    report = invoke(tmp_path, config_path=config_path, ops=ops)

    assert report["status"] == "PASS", report
    rendered = prometheus_default.read_text(encoding="utf-8")
    assert "--web.listen-address=127.0.0.1:9091" in rendered
    assert "Rendered by the site installer for" in rendered
    backups = sorted((tmp_path / "state/shared-path-prev").glob("prometheus*"))
    assert [b.name for b in backups] == ["prometheus", "prometheus-node-exporter"]
    # 副本是原件（发行版默认值），不是我们刚渲染的内容
    assert backups[0].read_text(encoding="utf-8").startswith('ARGS=""')

    # 重跑：现在文件带本站标记，仍然通过（幂等）
    second = invoke(tmp_path, config_path=config_path, ops=ops)
    assert second["status"] == "PASS", second


def test_another_sites_distro_default_blocks_fail_closed(tmp_path, monkeypatch):
    """带别站标记说明这台机器的监控栈归别的站点：fail-closed，且不写一个字节。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    _distro_units(tmp_path)
    foreign = "# Rendered by the site installer for /opt/other-site — do not edit by hand.\nARGS=\"--web.listen-address=127.0.0.1:9191\"\n"
    path = _seed_distro_default(tmp_path, "etc/default/prometheus", foreign)
    ops = InstallingFakeOps(hostname="control-i3.synthetic.invalid", mounts={str(tmp_path / "mnt/share")})

    report = invoke(tmp_path, config_path=_monitoring_site(tmp_path), ops=ops)

    assert report["status"] == "FAIL"
    assert "install_conflict" in codes(report)
    assert path.read_text(encoding="utf-8") == foreign

# ── #2265 时区一致性（声明 = 控制面 = Agent）──────────────────────────────


def _tz_ops(tmp_path: Path, timezone: str) -> FakeOps:
    return FakeOps(
        hostname="control-i3.synthetic.invalid",
        mounts={str(tmp_path / "mnt/share")},
        timezone=timezone,
    )


def test_timezone_mismatch_fails_closed_before_writes(tmp_path, monkeypatch):
    """控制面时区与 site.timezone 不一致 → FAIL install_timezone，且不往下走。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path, ops=_tz_ops(tmp_path, "America/Los_Angeles"))

    assert report["status"] == "FAIL"
    assert "install_timezone" in codes(report)
    check = next(c for c in report["checks"] if c["check_id"] == "install.s1.timezone")
    # message 同时给出实际值与声明值，操作者不用再猜
    assert "America/Los_Angeles" in check["message"] and "Asia/Shanghai" in check["message"]
    # 后续阶段不执行（部署根未落地）
    assert not (tmp_path / "opt/stp-control/backend").exists()


def test_timezone_unknown_is_blocked_not_passed(tmp_path, monkeypatch):
    """读不到主机时区 → BLOCKED（不猜、也不假装一致）。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path, ops=_tz_ops(tmp_path, ""))

    assert "timezone_unknown" in codes(report)
    assert next(c for c in report["checks"] if c["check_id"] == "install.s1.timezone")["status"] == "BLOCKED"


def test_timezone_aligned_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)
    report = invoke(tmp_path, ops=_tz_ops(tmp_path, "Asia/Shanghai"))

    assert report["status"] == "PASS", report
    assert "timezone_aligned" in codes(report)


# ── #2269：bundle 携带构建机本地状态须在 S0 fail-closed ─────────────────────


def test_bundle_carrying_dotenv_fails_closed_at_s0(tmp_path, monkeypatch):
    """bundle 内出现 `backend/.env` → S0 fail-closed，不得放行安装。

    摘要面只覆盖 backend/agent，`.env` 不进任何摘要——若不在此拦下，站点会静默
    继承构建机凭据（S2 落到 <deploy-root>/backend/.env，被后端启动时加载）。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    config_path, bindings, state_dir, target, site_id, data = prepare(tmp_path)
    bundle = Path(data["release"]["bundle"])
    (bundle / "backend" / ".env").write_text(
        "STP_FILE_SERVER_ADDRESS=build-host.invalid\n", encoding="utf-8",
    )
    report = invoke(tmp_path, dry_run=True)
    assert report["status"] == "FAIL", report
    assert "install.s0.hygiene" in {c["check_id"] for c in report["checks"]}, report["checks"]


def test_bundle_with_only_example_templates_is_not_hygiene_flagged(tmp_path, monkeypatch):
    """负向对照：入库模板 `*.example` 存在**不**应判违规（否则会破坏部署）。

    `deploy/postgres/.env.example` 等 8 个模板是部署文档要求 `cp` 的对象。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    config_path, bindings, state_dir, target, site_id, data = prepare(tmp_path)
    bundle = Path(data["release"]["bundle"])
    for name in (".env.example", ".env.backend.example", ".env.backend.internal.example"):
        (bundle / "backend" / name).write_text("A=\n", encoding="utf-8")
    report = invoke(tmp_path, dry_run=True)
    assert "install.s0.hygiene" not in {c["check_id"] for c in report["checks"]}, report["checks"]


def test_prefix_overlapping_deploy_roots_do_not_steal_shared_assets(tmp_path, monkeypatch):
    """#2274：站点 id 前缀重叠（…/stp-control 与 …/stp-controlB）时不得互相覆盖共享资产。

    归属判据此前是「整文件子串」：B 写下的 unit 引用 `…/stp-controlB/...`，A 的根
    `…/stp-control` 是它的真前缀 → A 把 B 的资产认作「本站」并覆盖（改指自己的部署根）。
    """
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)
    prepare(tmp_path)

    data = yaml.safe_load((tmp_path / "site.yaml").read_text(encoding="utf-8"))
    data["control_plane"]["deploy_root"] = str(tmp_path / "opt/stp-controlB")
    sibling = tmp_path / "site-sibling.yaml"
    sibling.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    # B（前缀更长的一方）先装，写下引用 …/stp-controlB 的 unit
    first = invoke(tmp_path, config_path=sibling)
    assert first["status"] == "PASS", first

    # A（前缀）再跑：B 的资产不是它的，必须 fail-closed 而不是覆盖
    second = invoke(tmp_path)

    assert second["status"] == "FAIL"
    assert "install_conflict" in codes(second), codes(second)


def test_run_stage_reports_unexpected_errors_instead_of_traceback(tmp_path, monkeypatch):
    """#2277：阶段里的未映射异常落成报告条目（stage_crashed），不穿成 traceback。"""
    monkeypatch.setattr(stages, "await_health", lambda *a, **k: True)

    def boom(_ctx):
        raise RuntimeError("stage exploded")

    # install.py 以 `from .stages import stage_s1_basics` 绑定，故要打在它自己的命名空间
    from tools.site_config import install as install_module

    monkeypatch.setattr(install_module, "stage_s1_basics", boom)
    prepare(tmp_path)

    report = invoke(tmp_path)

    assert report["status"] == "FAIL"
    assert "stage_crashed" in codes(report), codes(report)
