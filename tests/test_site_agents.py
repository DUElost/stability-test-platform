"""S5 Agent 接入（I4）：站点工具经本站 API 编排 Host 与 Agent 安装。

全部用合成配置 + Fake API：不发网络请求、不起 ansible、不碰真实 Host。
覆盖判据：Host 复用不重复注册、他站归属拒绝、重复触发不双启动、
响应丢失后重试幂等、错误目标 fail-closed、报告不泄密。
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from tools.site_config.agents import ApiError, stage_s5_agents
from tools.site_config.install import run_install
from tools.site_config.stages import InstallContext
from tools.site_config.validation import load_site_config

NOW = 1_700_000_000.0
PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"
AGENT_PASSWORD = "agent-ssh-secret-9374"
ADMIN_PASSWORD = "admin-secret-9374"
PUBLIC_URL = "https://control-i4.synthetic.invalid"
DIGEST_CODE = "sha256:" + "a" * 64
DIGEST_RESOURCES = "sha256:" + "b" * 64


def _heartbeat(offset: float = 0.0) -> str:
    return datetime.fromtimestamp(NOW - offset, tz=timezone.utc).isoformat()


def _host_id(ip: str) -> str:
    """Host ID 由站点 API 从 IP 分配（与 allocate_host_id 同形）。"""
    return ip.replace(".", "-")


def _manifest(path: Path) -> None:
    path.write_text(json.dumps({
        "manifest_version": 1,
        "product": {"version": "synthetic-2026.09.0"},
        "source": {"revision": "0123456789abcdef0123456789abcdef01234567"},
        "components": [
            {"name": "agent-code", "digest": DIGEST_CODE},
            {"name": "host-resources", "digest": DIGEST_RESOURCES},
        ],
        "database": {"schema_target": "cafe1234"},
        "compatibility": {
            "agent_protocol": ">=1.0,<2.0",
            "platforms": [{"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]}],
        },
        "provenance": {"attestation": "controlled_channel", "evidence_ref": "site_release_channel"},
    }), encoding="utf-8")


def _site_data(tmp_path: Path, bundle: Path, *, agents: list[dict] | None = None) -> dict:
    agents = agents if agents is not None else [_agent(tmp_path, "agent-01", "agent-01.synthetic.invalid")]
    return {
        "schema_version": 1,
        "site": {"id": "synthetic-i4", "display_name": "合成站点 I4", "timezone": "Asia/Shanghai"},
        "platform": {"os_family": "linux", "cpu_arch": "x86_64", "service_manager": "systemd"},
        "network": {"dependency_mode": "controlled_mirror"},
        "control_plane": {
            "target": "control-i4.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "control_ssh",
            "deploy_root": str(tmp_path / "opt/stp-control"),
            "deploy_user": "stp",
            "public_url": PUBLIC_URL,
            "security_profile": "production",
            "tls_ref": "site_tls",
        },
        "storage": {
            "provisioning": "existing_share",
            "protocol": "nfs",
            "target": "storage-i4.synthetic.invalid",
            "os": None,
            "ssh_user": None,
            "ssh_credential_ref": None,
            "share": "/srv/stp-export",
            "credential_ref": None,
            "mount_path": str(tmp_path / "mnt/share"),
        },
        "agents": agents,
        "dependencies": {"database_ref": "site_database", "redis_ref": "site_redis", "tools_profile": "synthetic"},
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


def _agent(tmp_path: Path, key: str, target: str) -> dict:
    return {
        "key": key,
        "target": target,
        "os": {"distribution": "debian", "version": "13"},
        "ssh_user": "bootstrap",
        "ssh_credential_ref": f"{key.replace('-', '_')}_ssh",
        # 同站点共享安装根：STP_SCRIPT_RUNTIME_ROOT 是站点级单值（异构根被模型拒绝）
        "install_root": str(tmp_path / "opt/stp-agent"),
        "local_aee_root": str(tmp_path / f"var/stp-aee-{key}"),
    }


class FakeApi:
    """An in-memory stand-in for the site API (records every call)."""

    def __init__(
        self,
        *,
        hosts: list[dict] | None = None,
        install_console: str = "SUCCESS",
        console_sequence: list[str] | None = None,
        create_status: int = 200,
        create_payload: object | None = None,
        install_status: int = 200,
        install_payload: object | None = None,
        devices: list[dict] | None = None,
        audit_urls: list[str] | None = None,
        host_overrides: dict | None = None,
        unreachable: bool = False,
        auth_error: bool = False,
        csrf_status: int = 403,
        specialties: list[dict] | None = None,
        plan_create: tuple[int, object] | None = None,
        run_trigger: tuple[int, object] | None = None,
        run_statuses: list[str] | None = None,
        run_jobs: list[dict] | None = None,
        run_events: list[dict] | None = None,
        digest_sequence: list[str] | None = None,
        resources_sequence: list[str] | None = None,
        identity_sequence: list[tuple[str, str]] | None = None,
        scan_status: int = 200,
        navigation: tuple[int, str] | None = None,
    ):
        self.hosts = hosts if hosts is not None else []
        self.created: list[dict] = []
        self.install_calls: list[tuple[str, dict]] = []
        self.status_calls = 0
        self.login_calls: list[str] = []
        self.plans_created: list[dict] = []
        self.run_calls: list[tuple[int, list[int]]] = []
        self.run_polls = 0
        self._console_sequence = list(console_sequence or [install_console])
        self._create_status = create_status
        self._create_payload = create_payload
        self._install_status = install_status
        self._install_payload = install_payload
        self._devices = devices if devices is not None else []
        self._audit_urls = audit_urls if audit_urls is not None else [PUBLIC_URL]
        self._host_overrides = host_overrides or {}
        self._unreachable = unreachable
        self._auth_error = auth_error
        self._csrf_status = csrf_status
        self._specialties = specialties if specialties is not None else [{"key": "mtbf"}]
        self._plan_create = plan_create
        self._run_trigger = run_trigger
        self._run_statuses = list(run_statuses or ["SUCCESS"])
        self._run_jobs = run_jobs
        self._run_events = run_events if run_events is not None else []
        self._digest_sequence = list(digest_sequence or [])
        self._resources_sequence = list(resources_sequence or [])
        self._identity_sequence = list(identity_sequence or [])
        self.host_reads = 0
        self._scan_status = scan_status
        self.scans = 0
        self._navigation = navigation

    # ── API surface ──────────────────────────────────────────────────────
    def login(self, username: str, password: str) -> str:
        if self._unreachable:
            raise ApiError("api_unreachable")
        if self._auth_error:
            raise ApiError("api_auth", status=401)
        self.login_calls.append(username)
        return "fake-token"

    def list_hosts(self) -> list[dict]:
        if self._unreachable:
            raise ApiError("api_unreachable")
        return list(self.hosts)

    def create_host(self, payload: dict) -> tuple[int, object]:
        if self._unreachable:
            raise ApiError("api_unreachable")
        if self._create_status != 200:
            return self._create_status, self._create_payload
        self.created.append(payload)
        host_id = _host_id(payload["ip"])
        host = {
            "id": host_id,
            "name": payload["name"],
            "ip": payload["ip"],
            "status": "OFFLINE",
            **self._host_overrides.get("created", {}),
        }
        self.hosts.append(host)
        return 200, host

    def request_install(self, host_id: str, options: dict) -> tuple[int, object]:
        self.install_calls.append((host_id, options))
        if self._install_status != 200:
            return self._install_status, self._install_payload
        return 200, {"ok": True, "console_run_id": f"con-{host_id}", "status": "running"}

    def install_status(self, host_id: str) -> dict:
        self.status_calls += 1
        index = min(self.status_calls - 1, len(self._console_sequence) - 1)
        console_status = self._console_sequence[index]
        summary = {
            "RUNNING": "running",
            "SUCCESS": "succeeded",
            "FAILED": "failed",
            "CANCELED": "canceled",
        }.get(console_status, "unknown")
        return {
            "host_id": host_id,
            "status": summary,
            "console_status": console_status,
            "console_found": True,
            "exit_code": 0 if console_status == "SUCCESS" else None,
            "room": f"console:con-{host_id}",
            "log_path": f"/opt/stp-control/logs/console/con-{host_id}.log",
        }

    def get_host(self, host_id: str) -> dict:
        self.host_reads += 1
        digest = None
        resources_digest = None
        if self._digest_sequence:
            digest = self._digest_sequence[min(self.host_reads - 1, len(self._digest_sequence) - 1)]
        if self._resources_sequence:
            resources_digest = self._resources_sequence[
                min(self.host_reads - 1, len(self._resources_sequence) - 1)
            ]
        identity = None
        if self._identity_sequence:
            identity = self._identity_sequence[
                min(self.host_reads - 1, len(self._identity_sequence) - 1)
            ]
        for host in self.hosts:
            if host["id"] == host_id:
                return {
                    **host,
                    "status": "ONLINE",
                    "last_heartbeat": _heartbeat(),
                    "agent_instance_id": identity[0] if identity else "inst-1",
                    "boot_id": identity[1] if identity else "boot-1",
                    **                    (
                        {
                            "agent_artifact_digest": digest if digest is not None else DIGEST_CODE,
                            "agent_resources_digest": (
                                resources_digest if resources_digest is not None else ""
                            ),
                        }
                        if (digest is not None or resources_digest is not None)
                        else {
                            "agent_artifact_digest": DIGEST_CODE,
                            "agent_resources_digest": DIGEST_RESOURCES,
                        }
                    ),
                    **self._host_overrides.get("detail", {}),
                }
        raise ApiError("api_response", status=404)

    def list_devices(self) -> list[dict]:
        return list(self._devices)

    def install_audit(self, host_id: str) -> list[dict]:
        return [{"details": {"agent_api_url": url}} for url in self._audit_urls]

    # ── S6 受控链 ────────────────────────────────────────────────────────
    def raw_write_probe(self, path: str) -> tuple[int, object]:
        if self._unreachable:
            raise ApiError("api_unreachable")
        return self._csrf_status, {"detail": "CSRF check failed"}

    def list_specialties(self) -> list[dict]:
        return list(self._specialties)

    def scan_scripts(self) -> tuple[int, object]:
        self.scans += 1
        return self._scan_status, {"registered": 1}

    def create_plan(self, payload: dict) -> tuple[int, object]:
        self.plans_created.append(payload)
        if self._plan_create is not None:
            return self._plan_create
        return 201, {"id": 7, "name": payload["name"]}

    def run_plan(self, plan_id: int, device_ids: list[int]) -> tuple[int, object]:
        self.run_calls.append((plan_id, list(device_ids)))
        if self._run_trigger is not None:
            return self._run_trigger
        return 200, {"id": 42, "status": "QUEUED"}

    def plan_run(self, run_id: int) -> dict:
        self.run_polls += 1
        index = min(self.run_polls - 1, len(self._run_statuses) - 1)
        jobs = self._run_jobs
        if jobs is None:
            jobs = [{
                "id": 1,
                "device_id": 5,
                "status": "COMPLETED",
                "step_traces": [{"step_key": "s6-noop", "exit_code": 0}],
            }]
        return {"id": run_id, "status": self._run_statuses[index], "jobs": jobs}

    def fetch_navigation(self) -> tuple[int, str]:
        if self._navigation is not None:
            return self._navigation
        return 200, (
            "synthetic-i4（合成站点 I4）synthetic-ops "
            "https://docs.synthetic.invalid/ops handover.json"
        )

    def plan_run_jobs(self, run_id: int) -> list[dict]:
        if self._run_jobs is not None:
            return list(self._run_jobs)
        return [{
            "id": 1,
            "device_id": 5,
            "status": "COMPLETED",
            "step_traces": [{"step_key": "s6-noop", "exit_code": 0}],
        }]

    def plan_run_events(self, run_id: int) -> list[dict]:
        return list(self._run_events)


@pytest.fixture
def site(tmp_path: Path):
    """Synthetic site: config + bindings + manifest + an InstallContext."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    _manifest(bundle / "release-manifest.json")
    config_path = tmp_path / "site.yaml"
    config_path.write_text(
        yaml.safe_dump(_site_data(tmp_path, bundle), allow_unicode=True), encoding="utf-8",
    )
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o700)
    files = {
        "site_admin": f"USERNAME=admin\nPASSWORD={ADMIN_PASSWORD}\n",
        "agent_01_ssh": f"USERNAME=bootstrap\nPASSWORD={AGENT_PASSWORD}\n",
    }
    for name, text in files.items():
        path = bindings / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700)

    def context(*, dry_run: bool = False, overrides: dict | None = None) -> InstallContext:
        # 每次都从文件重载：用例会在 fixture 之后改写 site.yaml（如去掉 manifest）
        current = load_site_config(config_path)
        values = {
            current.dependencies.database_ref: {"DATABASE_URL": "postgresql+asyncpg://stp:x@db:5432/stp"},
            current.dependencies.redis_ref: {"REDIS_URL": "redis://redis:6379/0"},
            current.security.initial_admin_ref: {"USERNAME": "admin", "PASSWORD": ADMIN_PASSWORD},
        }
        values.update(overrides or {})
        return InstallContext(
            config=current,
            config_path=config_path,
            bundle=bundle,
            bindings_dir=bindings,
            state_dir=state_dir,
            ops=None,  # S5 不经 ops 触碰目标机：它只走 API
            dry_run=dry_run,
            binding_values=values,
        )

    # 便于 S6 用例直接按路径调用 verify_site（函数对象上挂路径）
    context.config_path = config_path
    context.bindings_dir = bindings
    return context


def _codes(checks) -> list[str]:
    return [check.code for check in checks]


def _status(checks, check_id: str) -> str:
    return next(check.status for check in checks if check.check_id == check_id)


def _run(ctx, api, **kwargs):
    kwargs.setdefault("progress", lambda _: None)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("sleep", lambda _: None)
    kwargs.setdefault("poll_timeout", 30.0)
    kwargs.setdefault("digest_timeout", 0.0)
    kwargs.setdefault("identity_timeout", 0.0)
    # 默认不让真探针跑 ssh：用例里显式覆盖
    kwargs.setdefault("sudo_probe", lambda ops, agent, binding: (True, ""))
    return stage_s5_agents(ctx, api=api, **kwargs)


class TestTargetSudoProbe:
    def test_probe_runs_before_any_install_and_stops_on_failure(self, site):
        """目标机 sudo 不可用 → 触发安装之前就 FAIL，且不创建任何 Host。"""
        api = FakeApi()
        checks = _run(
            site(), api,
            sudo_probe=lambda ops, agent, binding: (False, "sudo_unavailable"),
        )

        assert _status(checks, "install.s5.sudo") == "FAIL"
        assert "target_sudo_unavailable" in _codes(checks)
        assert api.created == [], "预检失败后仍在建 Host"
        assert api.install_calls == [], "预检失败后仍触发了安装"

    def test_probe_failure_carries_the_su_recipe_as_fix(self, site):
        checks = _run(
            site(), FakeApi(),
            sudo_probe=lambda ops, agent, binding: (False, "sudo_unavailable"),
        )
        check = next(c for c in checks if c.check_id == "install.s5.sudo")
        assert "usermod -aG sudo" in check.remediation
        assert "visudo -cf" in check.remediation
        assert "NOPASSWD: ALL" in check.remediation
        # message 要带原因，操作者才知道是哪一类问题
        assert "sudo_unavailable" in check.message

    def test_ssh_level_failure_points_at_host_key_and_network(self, site):
        checks = _run(
            site(), FakeApi(),
            sudo_probe=lambda ops, agent, binding: (False, "ssh_probe_failed"),
        )
        check = next(c for c in checks if c.check_id == "install.s5.sudo")
        assert check.code == "ssh_probe_failed"
        assert "ssh-keyscan" in check.remediation

    def test_unrun_probe_is_blocked_and_never_claims_readiness(self, site):
        checks = _run(
            site(), FakeApi(),
            sudo_probe=lambda ops, agent, binding: (None, "probe_not_run"),
        )
        assert _status(checks, "install.s5.sudo") == "BLOCKED"
        assert "probe_not_run" in _codes(checks)

    def test_happy_path_reports_sudo_ready(self, site):
        checks = _run(site(), FakeApi())
        assert _status(checks, "install.s5.sudo") == "PASS"
        assert "target_sudo_ready" in _codes(checks)


class TestIdentityWait:
    def test_first_heartbeat_may_lack_identity_and_is_awaited(self, site):
        """首次接入的第一条心跳可能只带部分字段：要有界等待，不能立刻判失败。"""
        slept: list[float] = []
        api = FakeApi(identity_sequence=[("", ""), ("inst-9", "boot-9")])

        checks = _run(
            site(), api,
            identity_timeout=30.0, poll_interval=5.0,
            sleep=lambda seconds: slept.append(seconds),
        )

        assert api.host_reads >= 2, "identity 还没刷新就下了结论"
        assert slept == [5.0]
        assert _status(checks, "install.s5.identity") == "PASS"

    def test_identity_missing_beyond_the_window_fails(self, site):
        api = FakeApi(identity_sequence=[("", "")])
        checks = _run(site(), api, identity_timeout=0.0, poll_interval=5.0, sleep=lambda _: None)
        assert "agent_identity" in _codes(checks)


class TestProbeTargetSudo:
    class Ops:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.argv: list[tuple[str, ...]] = []
            self.env: dict[str, str] = {}

        def command_exists(self, name: str) -> bool:
            return True

        def run(self, argv, **kwargs):
            self.argv.append(tuple(str(item) for item in argv))
            self.env = dict(kwargs.get("env") or {})
            from tools.site_config.ops import CommandResult

            return CommandResult(tuple(str(item) for item in argv), 0, self.stdout)

    def _agent(self):
        from types import SimpleNamespace

        return SimpleNamespace(target="10.99.0.31")

    def test_password_never_reaches_argv(self):
        from tools.site_config.agents import probe_target_sudo

        ops = self.Ops("STP_SUDO_OK\n")
        binding = {"USERNAME": "ops", "PASSWORD": "DoNotLeak-9374"}
        available, reason = probe_target_sudo(ops, self._agent(), binding)

        assert (available, reason) == (True, "")
        joined = " ".join(" ".join(call) for call in ops.argv)
        assert "DoNotLeak-9374" not in joined
        assert ops.env.get("SSHPASS") == "DoNotLeak-9374"
        assert "sudo -n true" in joined and "-o ConnectTimeout=8" in joined

    def test_key_binding_uses_the_key_file_and_no_sshpass(self):
        from tools.site_config.agents import probe_target_sudo

        ops = self.Ops("STP_SUDO_FAIL\n")
        available, reason = probe_target_sudo(
            ops, self._agent(), {"USERNAME": "ops", "PRIVATE_KEY_PATH": "/root/.ssh/id_ed25519"},
        )

        assert (available, reason) == (False, "sudo_unavailable")
        joined = " ".join(" ".join(call) for call in ops.argv)
        assert "-i /root/.ssh/id_ed25519" in joined
        assert "sshpass" not in joined

    def test_ssh_failures_are_classified_for_the_right_fix(self):
        from tools.site_config.agents import probe_target_sudo

        cases = {
            "Host key verification failed.\n": "ssh_host_key_unverified",
            "Permission denied (publickey,password).\n": "ssh_credentials_rejected",
            "ssh: connect to host 10.99.0.31 port 22: Connection timed out\n": "ssh_unreachable",
            "something else\n": "ssh_probe_failed",
        }
        for stdout, expected in cases.items():
            assert probe_target_sudo(
                self.Ops(stdout), self._agent(), {"USERNAME": "ops", "PASSWORD": "x"},
            ) == (False, expected), stdout

    def test_sudo_refusal_carries_the_targets_own_words(self):
        from tools.site_config.agents import probe_target_sudo

        ops = self.Ops("sudo: a password is required\nSTP_SUDO_FAIL\n")
        available, reason = probe_target_sudo(
            ops, self._agent(), {"USERNAME": "ops", "PASSWORD": "x"},
        )
        assert available is False
        assert reason.startswith("sudo_unavailable")
        assert "a password is required" in reason

    def test_sshpass_unknown_host_key_exit_code_is_classified(self):
        """sshpass 6 = 主机公钥未知：严格模式下输出可能为空，只能靠退出码。"""
        from tools.site_config.agents import probe_target_sudo

        class Ops(self.Ops):
            def run(self, argv, **kwargs):
                from tools.site_config.ops import CommandResult

                return CommandResult(tuple(str(item) for item in argv), 6, "", "")

        assert probe_target_sudo(
            Ops(""), self._agent(), {"USERNAME": "ops", "PASSWORD": "x"},
        ) == (False, "ssh_host_key_unverified")

    def test_stderr_is_used_when_stdout_is_empty(self):
        """ssh 把错误写 stderr：只看 stdout 会把主机键问题误报成探针失败。"""
        from tools.site_config.agents import probe_target_sudo

        class Ops(self.Ops):
            def run(self, argv, **kwargs):
                from tools.site_config.ops import CommandResult

                argv = tuple(str(item) for item in argv)
                return CommandResult(argv, 255, "", "Host key verification failed.\n")

        assert probe_target_sudo(
            Ops(""), self._agent(), {"USERNAME": "ops", "PASSWORD": "x"},
        ) == (False, "ssh_host_key_unverified")


class TestHappyPath:
    def test_creates_host_and_asserts_agent(self, site):
        api = FakeApi()
        checks = _run(site(), api)

        assert _codes(checks).count("FAIL") == 0, [asdict(c) for c in checks]
        assert api.created[0]["name"] == "synthetic-i4-agent-01"
        assert api.created[0]["ssh_auth_type"] == "password"
        assert api.install_calls == [
            (_host_id("agent-01.synthetic.invalid"), {
                "agent_install_root": str(site().config.agents[0].install_root),
                "agent_local_aee_root": str(site().config.agents[0].local_aee_root),
                # 站点标准：Agent 的 STP_AEE_NFS_ROOT = 站点 storage.mount_path
                "agent_nfs_root": str(site().config.storage.mount_path),
            })
        ]
        assert _status(checks, "install.s5.heartbeat") == "PASS"
        assert _status(checks, "install.s5.digest") == "PASS"
        assert _status(checks, "install.s5.endpoint") == "PASS"

    def test_existing_host_is_reused_without_creating(self, site):
        """重跑幂等：同 IP 同名的 Host 直接复用，不再 POST /hosts。"""
        api = FakeApi(hosts=[{
            "id": "agent-01.synthetic.invalid",
            "name": "synthetic-i4-agent-01",
            "ip": "agent-01.synthetic.invalid",
            "status": "OFFLINE",
        }])
        checks = _run(site(), api)

        assert api.created == []
        assert _status(checks, "install.s5.host") == "PASS"

    def test_install_already_running_is_joined_not_restarted(self, site):
        """响应丢失后重试：409 + console_run_id 接到现有 run，不启动第二个。"""
        api = FakeApi(install_status=409, install_payload={
            "detail": {"message": "install already in progress", "console_run_id": "con-existing"},
        })
        checks = _run(site(overrides={}), api)

        assert _status(checks, "install.s5.install") in {"PASS", "FAIL"}
        assert api.install_calls[0][0] == _host_id("agent-01.synthetic.invalid")
        # 接上现有 run 后继续轮询，而不是报「无法启动」
        assert api.status_calls >= 1
        assert "install_trigger_failed" not in _codes(checks)

    def test_lost_create_response_reconciles_instead_of_duplicating(self, site):
        """创建响应丢失：409 后重新查询发现 Host 已存在 → 复用同一 ID。"""
        race_states = {"done": False}

        class RacingApi(FakeApi):
            def create_host(self, payload):
                self.created.append(payload)
                return 409, {"detail": {"code": "HOST_IDENTITY_CONFLICT", "field": "ip"}}

            def list_hosts(self):
                if self.created and not race_states["done"]:
                    race_states["done"] = True
                    return [{
                        "id": "agent-01.synthetic.invalid",
                        "name": "synthetic-i4-agent-01",
                        "ip": "agent-01.synthetic.invalid",
                    }]
                return list(self.hosts)

        api = RacingApi()
        checks = _run(site(), api)

        assert api.created and len(api.created) == 1
        assert _status(checks, "install.s5.host") == "PASS"
        assert "host_create_failed" not in _codes(checks)


class TestFailClosed:
    def test_foreign_host_on_same_ip_is_rejected(self, site):
        """他站/他角色占用同 IP：名称不符即停，不安装、不改名。"""
        api = FakeApi(hosts=[{
            "id": "other-site-agent",
            "name": "other-site-agent-01",
            "ip": "agent-01.synthetic.invalid",
        }])
        checks = _run(site(), api)

        assert "host_conflict" in _codes(checks)
        assert api.created == []
        assert api.install_calls == []

    def test_declared_name_taken_elsewhere_is_a_conflict(self, site):
        """同名占用了另一个地址：命名冲突即停，不换名绕过。"""
        api = FakeApi(
            hosts=[{"id": "198-51-100-9", "name": "synthetic-i4-agent-01", "ip": "198.51.100.9"}],
            create_status=409,
            create_payload={"detail": {"code": "HOST_IDENTITY_CONFLICT", "field": "name"}},
        )
        checks = _run(site(), api)

        assert "host_conflict" in _codes(checks)
        assert "host_create_failed" not in _codes(checks)
        assert api.install_calls == []

    def test_retired_host_is_rejected(self, site):
        api = FakeApi(hosts=[{
            "id": "agent-01.synthetic.invalid",
            "name": "synthetic-i4-agent-01",
            "ip": "agent-01.synthetic.invalid",
            "retired_at": "2026-09-01T00:00:00Z",
        }])
        checks = _run(site(), api)

        assert "host_retired" in _codes(checks)
        assert api.install_calls == []

    def test_unconfigured_install_url_is_reported(self, site):
        """控制面缺 STP_AGENT_INSTALL_API_URL：400 → 明确失败，不重试轮询。"""
        api = FakeApi(install_status=400, install_payload={
            "detail": {"code": "AGENT_INSTALL_NOT_CONFIGURED", "message": "…"},
        })
        checks = _run(site(), api)

        assert "agent_install_unconfigured" in _codes(checks)
        assert api.status_calls == 0

    def test_missing_ansible_dependency_is_reported(self, site):
        api = FakeApi(install_status=501, install_payload={"detail": "缺少依赖"})
        checks = _run(site(), api)

        assert "install_dependency" in _codes(checks)

    def test_api_unreachable_is_reported(self, site):
        checks = _run(site(), FakeApi(unreachable=True))

        assert "api_unreachable" in _codes(checks)
        assert _status(checks, "install.s5.auth") == "FAIL"

    def test_rejected_admin_credentials_are_reported(self, site):
        checks = _run(site(), FakeApi(auth_error=True))

        assert "api_auth" in _codes(checks)
        assert "install.s5.auth" in {check.check_id for check in checks}

    def test_install_run_failure_is_reported(self, site):
        checks = _run(site(), FakeApi(install_console="FAILED"))

        assert "agent_install_failed" in _codes(checks)

    def test_canceled_install_is_reported_as_canceled_not_failed(self, site):
        """取消 ≠ 脚本失败：分开报，并带判定依据与日志路径（#2225 / ADR-0044）。

        现场实景：安装被取消（显式取消与控制面重启在 console 上是同一终态），
        合并报成 agent_install_failed 让操作者去目标机找不存在的错误。
        """
        checks = _run(site(), FakeApi(install_console="CANCELED"))
        check = next(c for c in checks if c.check_id == "install.s5.install")

        assert check.code == "agent_install_canceled"
        assert "agent_install_failed" not in _codes(checks)
        assert "console=CANCELED" in check.message
        assert check.message.rstrip().endswith(").")  # 带 log=... 的 detail
        assert "deploy/agent/install.sh" in check.remediation

    def test_lost_run_record_is_reported_as_canceled_not_failed(self, site):
        """运行记录消失（控制面重启/记录过期）按取消报——那也是生命周期边界，不是脚本失败。"""
        class LostApi(FakeApi):
            def install_status(self, host_id: str) -> dict:
                return {
                    "status": "lost",
                    "console_status": None,
                    "console_found": False,
                    "log_path": "/opt/stp-control/logs/console/con-gone.log",
                }

        checks = _run(site(), LostApi())
        check = next(c for c in checks if c.check_id == "install.s5.install")

        assert check.code == "agent_install_canceled"
        assert "status=lost" in check.message and "con-gone.log" in check.message
        assert "agent_install_failed" not in _codes(checks)

    def test_script_failure_still_reports_failed(self, site):
        """回归守卫：真正的脚本失败（console FAILED）仍报 failed，别被取消分支吃掉。"""
        class FailedJobApi(FakeApi):
            def install_status(self, host_id: str) -> dict:
                return {"status": "failed", "console_status": "FAILED", "console_found": True}

        checks = _run(site(), FailedJobApi())

        assert "agent_install_failed" in _codes(checks)
        assert "agent_install_canceled" not in _codes(checks)

    def test_install_timeout_is_reported(self, site):
        checks = _run(site(), FakeApi(install_console="RUNNING"), poll_timeout=0.0)

        assert "install_timeout" in _codes(checks)

    def test_offline_agent_is_reported(self, site):
        api = FakeApi(host_overrides={"detail": {"status": "OFFLINE"}})
        checks = _run(site(), api)

        assert "agent_offline" in _codes(checks)
        assert "install.s5.digest" not in {check.check_id for check in checks}

    def test_stale_heartbeat_is_reported(self, site):
        api = FakeApi(host_overrides={"detail": {"last_heartbeat": _heartbeat(offset=600)}})
        checks = _run(site(), api)

        assert "agent_offline" in _codes(checks)

    def test_missing_agent_identity_is_reported(self, site):
        api = FakeApi(host_overrides={"detail": {"agent_instance_id": "", "boot_id": ""}})
        checks = _run(site(), api)

        assert "agent_identity" in _codes(checks)

    def test_content_digest_mismatch_is_reported(self, site):
        api = FakeApi(host_overrides={"detail": {"agent_artifact_digest": "sha256:" + "f" * 64}})
        checks = _run(site(), api)

        assert "agent_digest_mismatch" in _codes(checks)

    def test_digest_report_is_awaited(self, site):
        """摘要在安装后才由 Agent 下一次心跳带上：断言必须等待而不是抢先失败。"""
        slept: list[float] = []
        api = FakeApi(digest_sequence=["", DIGEST_CODE], resources_sequence=["", DIGEST_RESOURCES])

        checks = _run(
            site(), api,
            digest_timeout=30.0,
            poll_interval=5.0,
            sleep=lambda seconds: slept.append(seconds),
        )

        assert api.host_reads >= 2, "未重读 Host 就下了结论"
        assert slept == [5.0]
        assert _status(checks, "install.s5.digest") == "PASS"

    def test_digest_wait_covers_a_stale_resources_value(self, site):
        """重装场景：code 早已有值、resources 还是上一版 → 必须继续等，不能立即比对。

        238 实测：Agent 端逐拍重读摘要（#1943），文件写好要等一个心跳周期生效；
        原来的「任一非空就收工」会在 resources 仍是旧值时下结论并报 mismatch。
        """
        slept: list[float] = []
        stale = "sha256:" + "0" * 64
        api = FakeApi(resources_sequence=[stale, DIGEST_RESOURCES])

        checks = _run(
            site(), api,
            digest_timeout=30.0,
            poll_interval=5.0,
            sleep=lambda seconds: slept.append(seconds),
        )

        assert api.host_reads >= 2, "resources 还没刷新就下了结论"
        assert slept == [5.0]
        assert _status(checks, "install.s5.digest") == "PASS"

    def test_stale_resources_beyond_the_window_is_a_mismatch(self, site):
        """窗口耗尽仍是旧值：如实报 mismatch（不许掩盖）。"""
        stale = "sha256:" + "0" * 64
        api = FakeApi(resources_sequence=[stale])

        checks = _run(site(), api, digest_timeout=0.0, poll_interval=5.0, sleep=lambda _: None)

        assert "agent_digest_mismatch" in _codes(checks)

    def test_missing_content_digest_is_not_a_pass(self, site):
        """Agent 未上报摘要 ≠ 内容一致：不能当 S5 已通过。"""
        api = FakeApi(host_overrides={"detail": {
            "agent_artifact_digest": "", "agent_resources_digest": "",
        }})
        checks = _run(site(), api)

        assert "agent_digest_missing" in _codes(checks)

    def test_foreign_api_url_in_audit_is_rejected(self, site):
        api = FakeApi(audit_urls=["https://other-site.invalid"])
        checks = _run(site(), api)

        assert "agent_endpoint" in _codes(checks)

    def test_missing_agent_binding_stops_before_api(self, site, tmp_path):
        ctx = site()
        (tmp_path / "bindings/agent_01_ssh").unlink()
        api = FakeApi()
        checks = _run(ctx, api)

        assert "binding_file" in _codes(checks)
        assert api.login_calls == []

    def test_wrong_binding_shape_is_rejected(self, site, tmp_path):
        path = tmp_path / "bindings/agent_01_ssh"
        path.write_text("USERNAME=bootstrap\nPASSWORD=x\nTOKEN=extra\n", encoding="utf-8")
        os.chmod(path, 0o600)
        checks = _run(site(), FakeApi())

        assert "binding_content" in _codes(checks)

    def test_key_binding_requires_owner_only_mode(self, site, tmp_path):
        key = tmp_path / "id_ed25519"
        key.write_text("PRIVATE KEY\n", encoding="utf-8")
        os.chmod(key, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        path = tmp_path / "bindings/agent_01_ssh"
        path.write_text(f"USERNAME=bootstrap\nPRIVATE_KEY_PATH={key}\n", encoding="utf-8")
        os.chmod(path, 0o600)

        checks = _run(site(), FakeApi())

        assert "agent_key_permissions" in _codes(checks)

    def test_failure_stops_before_next_agent(self, site, tmp_path):
        """失败即停：第一个 Agent 失败后不再触碰第二个。"""
        config_path = tmp_path / "site.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        data["agents"].append(_agent(tmp_path, "agent-02", "agent-02.synthetic.invalid"))
        config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        for name in ("agent_02_ssh",):
            path = tmp_path / "bindings" / name
            path.write_text("USERNAME=bootstrap\nPASSWORD=x\n", encoding="utf-8")
            os.chmod(path, 0o600)

        api = FakeApi(hosts=[{
            "id": "occupied", "name": "other-site-agent-01", "ip": "agent-01.synthetic.invalid",
        }])
        checks = _run(site(), api)

        assert "host_conflict" in _codes(checks)
        assert [call for call in api.install_calls] == []


class TestStorageAssertion:
    """#2181：自建中心存储的站点必须断言 Agent 真的挂上了同一个路径。"""

    @staticmethod
    def _exporting(site):
        """把合成站点改成自建导出（local_mount + export_to_agents）。"""
        data = yaml.safe_load(site.config_path.read_text(encoding="utf-8"))
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
        site.config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        return data["storage"]["mount_path"]

    def test_reported_mount_passes(self, site):
        mount_path = self._exporting(site)
        api = FakeApi(host_overrides={"detail": {"mount_status": {mount_path: {"ok": True}}}})

        checks = _run(site(), api)

        assert _status(checks, "install.s5.storage") == "PASS"
        assert "shared_storage_mounted" in _codes(checks)
        assert _codes(checks).count("FAIL") == 0, [asdict(c) for c in checks]

    def test_unreported_mount_fails_closed_and_stops(self, site):
        """装了但没挂上：AEE 会静默落本地，中心存储永远是空的——必须 FAIL 且不再继续。"""
        self._exporting(site)
        api = FakeApi(devices=[{"host_id": _host_id("agent-01.synthetic.invalid"), "serial": "S1"}])

        checks = _run(site(), api)

        assert _status(checks, "install.s5.storage") == "FAIL"
        assert "shared_storage_not_mounted" in _codes(checks)
        # 失败即停：不再断言设备发现
        assert all(check.check_id != "install.s5.devices" for check in checks)

    def test_reported_mount_at_another_path_is_not_accepted(self, site):
        """上报了别的路径（例如本机 SSD 落点）不算共享存储已挂。"""
        self._exporting(site)
        api = FakeApi(host_overrides={"detail": {"mount_status": {"/mnt/hdd": {"ok": True}}}})

        checks = _run(site(), api)

        assert "shared_storage_not_mounted" in _codes(checks)
        check = next(c for c in checks if c.check_id == "install.s5.storage")
        assert "/mnt/hdd" in check.message, "失败消息要列出实际上报的路径"

    def test_site_without_export_is_blocked_not_failed(self, site):
        """外部分享站点不由本站导出：如实 BLOCKED，不假装验过也不误报失败。"""
        checks = _run(site(), FakeApi())

        assert _status(checks, "install.s5.storage") == "BLOCKED"
        assert "export_not_enabled" in _codes(checks)
        assert _codes(checks).count("FAIL") == 0


class TestScopes:
    def test_devices_absent_is_blocked_not_failed(self, site):
        checks = _run(site(), FakeApi())
        assert _status(checks, "install.s5.devices") == "BLOCKED"
        assert _codes(checks).count("FAIL") == 0

    def test_discovered_device_is_reported(self, site):
        api = FakeApi(devices=[{
            "host_id": _host_id("agent-01.synthetic.invalid"), "serial": "SYNTH0001",
        }])
        checks = _run(site(), api)

        assert _status(checks, "install.s5.devices") == "PASS"

    def test_dry_run_never_calls_the_api(self, site):
        api = FakeApi()
        checks = _run(site(dry_run=True), api)

        assert api.login_calls == []
        assert api.created == []
        assert _status(checks, "install.s5.plan") == "PASS"
        assert all(check.status != "FAIL" for check in checks)

    def test_missing_manifest_blocks_agent_stage(self, site, tmp_path):
        config_path = tmp_path / "site.yaml"
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        data["release"]["manifest"] = None
        config_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
        checks = _run(site(), FakeApi())

        assert "manifest_missing" in _codes(checks)


class TestRedaction:
    def test_report_never_contains_binding_values(self, site):
        api = FakeApi()
        checks = _run(site(), api)
        rendered = json.dumps([asdict(check) for check in checks], ensure_ascii=False)

        assert AGENT_PASSWORD not in rendered
        assert ADMIN_PASSWORD not in rendered
        assert PRIVATE_MARKER not in rendered

    def test_created_host_payload_is_not_echoed_in_checks(self, site):
        api = FakeApi()
        checks = _run(site(), api)
        rendered = json.dumps([asdict(check) for check in checks], ensure_ascii=False)

        # 凭据随请求发送，但绝不回显到检查消息/修复建议
        assert api.created[0]["ssh_password"] == AGENT_PASSWORD
        assert AGENT_PASSWORD not in rendered


class TestInstallWiring:
    def test_run_install_reaches_s5_only_with_the_flag(self, tmp_path, monkeypatch):
        """--through-agents 才执行 S5；默认仍停在 S4。"""
        import tools.site_config.install as install_module

        calls: list[str] = []

        def fake_stage(ctx):
            calls.append("s5")
            return []

        monkeypatch.setattr(install_module, "stage_s5_agents", fake_stage)
        monkeypatch.setattr(install_module, "stage_s1_basics", lambda ctx: [])
        monkeypatch.setattr(install_module, "stage_s2_release_env", lambda ctx: [])
        monkeypatch.setattr(install_module, "stage_s4_entry", lambda ctx: [])
        monkeypatch.setattr(install_module, "_digest_bundle", lambda ctx: {
            "agent-code": DIGEST_CODE, "host-resources": DIGEST_RESOURCES,
        })
        monkeypatch.setattr(install_module, "_hostname_evidence", lambda ctx: True)
        monkeypatch.setattr(install_module, "_code_head", lambda ctx: "cafe1234")

        site_fixture = _prepare_minimal_site(tmp_path)
        ctx_args = {
            "bindings_dir": site_fixture["bindings"],
            "state_dir": site_fixture["state"],
            "confirm_site": "synthetic-i4",
            "confirm_target": "control-i4.synthetic.invalid",
        }

        class FakeOps:
            def os_release(self):
                return {"ID": "debian", "VERSION_ID": "13"}

            def machine(self):
                return "x86_64"

            def hostname(self):
                return "control-i4.synthetic.invalid"

            def local_addresses(self):
                return {"control-i4.synthetic.invalid"}

        minimal = load_site_config(site_fixture["config"])
        monkeypatch.setattr(install_module, "load_site_config", lambda path: minimal)

        def fake_bindings(ctx):
            # 真实 load_bindings 会填充 ctx.binding_values；S3 的探针依赖它。
            ctx.binding_values[minimal.dependencies.database_ref] = {
                "DATABASE_URL": "postgresql+asyncpg://stp:x@db:5432/stp",
            }
            return []

        monkeypatch.setattr(install_module, "load_bindings", fake_bindings)
        monkeypatch.setattr(install_module, "stage_s3_database_admin", lambda ctx, state, head: [])

        without = run_install(
            site_fixture["config"], **ctx_args, ops=FakeOps(),
            db_probe=lambda dsn: ("empty", None),
        )
        assert calls == []
        assert [stage["stage"] for stage in without["stages"]] == ["S0", "S1", "S2", "S3", "S4"]

        with_agents = run_install(
            site_fixture["config"], **ctx_args, through_agents=True, ops=FakeOps(),
            db_probe=lambda dsn: ("empty", None),
        )
        assert calls == ["s5"]
        assert [stage["stage"] for stage in with_agents["stages"]][-1] == "S5"
        assert "S6" in with_agents["summary"]


def _prepare_minimal_site(tmp_path: Path) -> dict:
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    _manifest(bundle / "release-manifest.json")
    config_path = tmp_path / "site.yaml"
    config_path.write_text(
        yaml.safe_dump(_site_data(tmp_path, bundle), allow_unicode=True), encoding="utf-8",
    )
    bindings = tmp_path / "bindings"
    bindings.mkdir(mode=0o700, exist_ok=True)
    for name, text in {
        "site_admin": f"USERNAME=admin\nPASSWORD={ADMIN_PASSWORD}\n",
        "agent_01_ssh": f"USERNAME=bootstrap\nPASSWORD={AGENT_PASSWORD}\n",
    }.items():
        path = bindings / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, 0o600)
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    return {"config": config_path, "bindings": bindings, "state": state_dir}


# ── S6：受控验收（verify.py）────────────────────────────────────────────


def _live_host(**overrides) -> dict:
    return {
        "id": _host_id("agent-01.synthetic.invalid"),
        "name": "synthetic-i4-agent-01",
        "ip": "agent-01.synthetic.invalid",
        "status": "ONLINE",
        "last_heartbeat": _heartbeat(),
        **overrides,
    }


def _verified_device(**overrides) -> dict:
    return {
        "id": 5,
        "serial": "SYNTH0001",
        "status": "ONLINE",
        "host_id": _host_id("agent-01.synthetic.invalid"),
        **overrides,
    }


def _run_verify(site, api, **kwargs):
    from tools.site_config.verify import verify_site

    kwargs.setdefault("progress", lambda _: None)
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("sleep", lambda _: None)
    kwargs.setdefault("poll_interval", 0.0)
    kwargs.setdefault("run_timeout", 0.0)
    return verify_site(site.config_path, bindings_dir=site.bindings_dir, api=api, **kwargs)


def _report_codes(report) -> list[str]:
    return [check["code"] for check in report["checks"]]


def _report_status(report, check_id: str) -> str:
    return next(check["status"] for check in report["checks"] if check["check_id"] == check_id)


class TestVerifyS6:
    def test_happy_path_reports_chain_and_blocks_uncovered(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()])
        report = _run_verify(site, api)

        assert report["status"] == "PASS", report
        assert _report_status(report, "verify.s6.csrf") == "PASS"
        assert _report_status(report, "verify.s6.chain") == "PASS"
        assert api.plans_created[0]["name"] == "s6-noop-synthetic-i4"
        assert api.scans == 1, "建计划前必须先注册脚本目录"
        step = api.plans_created[0]["steps"][0]
        assert step["script_name"] == "noop"
        # 缺 timeout_seconds 会被平台以 422 INVALID_LIFECYCLE 拒绝（实验室实测）
        assert isinstance(step["timeout_seconds"], int)
        assert api.run_calls == [(7, [5])]
        # 未覆盖路径必须显式 BLOCKED，不能静默算通过
        assert _report_status(report, "verify.s6.storage") == "BLOCKED"
        assert _report_status(report, "verify.s6.scan_upload_merge") == "BLOCKED"
        assert _report_status(report, "verify.s6.watcher") == "BLOCKED"

    def test_missing_navigation_entry_is_reported(self, site):
        """MS-13：导航不可达属 FAIL；它不影响平台入口，但验收要看见。"""
        api = FakeApi(hosts=[_live_host()], navigation=(404, ""))
        report = _run_verify(site, api)

        assert "navigation_missing" in _report_codes(report)

    def test_navigation_without_site_identity_is_rejected(self, site):
        api = FakeApi(hosts=[_live_host()], navigation=(200, "<html>generic</html>"))
        report = _run_verify(site, api)

        assert "navigation_missing" in _report_codes(report)

    def test_csrf_guard_must_refuse_cross_origin_write(self, site):
        api = FakeApi(csrf_status=401, devices=[_verified_device()])
        report = _run_verify(site, api)

        assert report["status"] == "FAIL"
        assert "csrf_not_enforced" in _report_codes(report)
        # CSRF 未生效即停：不再驱动受控链
        assert api.plans_created == []

    def test_no_device_blocks_the_chain_without_failing(self, site):
        report = _run_verify(site, FakeApi(hosts=[_live_host()]))

        assert report["status"] == "PASS", report
        assert _report_status(report, "verify.s6.devices") == "BLOCKED"
        assert _report_status(report, "verify.s6.chain") == "BLOCKED"

    def test_declared_serial_must_be_discovered(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device(serial="OTHER")])
        report = _run_verify(site, api, device_serial="SYNTH0001")

        assert "device_not_found" in _report_codes(report)
        assert api.plans_created == []

    def test_declared_serial_must_be_online(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device(status="OFFLINE")])
        report = _run_verify(site, api, device_serial="SYNTH0001")

        assert "device_unavailable" in _report_codes(report)

    def test_offline_host_is_reported(self, site):
        api = FakeApi(
            hosts=[_live_host(status="OFFLINE", last_heartbeat=_heartbeat(offset=600))],
            devices=[_verified_device()],
        )
        report = _run_verify(site, api)

        assert "agent_offline" in _report_codes(report)

    def test_missing_host_is_reported(self, site):
        report = _run_verify(site, FakeApi(devices=[_verified_device()]))

        assert "host_not_found" in _report_codes(report)

    def test_failed_run_is_reported(self, site):
        api = FakeApi(hosts=[_live_host()], 
            devices=[_verified_device()],
            run_statuses=["FAILED"],
            run_jobs=[{"id": 1, "device_id": 5, "status": "FAILED", "step_traces": []}],
        )
        report = _run_verify(site, api)

        assert "run_failed" in _report_codes(report)

    def test_run_without_step_traces_is_not_a_pass(self, site):
        api = FakeApi(hosts=[_live_host()], 
            devices=[_verified_device()],
            run_jobs=[{"id": 1, "device_id": 5, "status": "COMPLETED", "step_traces": []}],
        )
        report = _run_verify(site, api)

        assert "run_evidence_missing" in _report_codes(report)

    def test_run_on_another_device_is_not_a_pass(self, site):
        api = FakeApi(
            hosts=[_live_host()],
            devices=[_verified_device()],
            run_jobs=[{
                "id": 1, "device_id": 99, "status": "COMPLETED",
                "step_traces": [{"step_key": "s6-noop", "exit_code": 0}],
            }],
        )
        report = _run_verify(site, api)

        # 证据落在那台设备之外 → 视为没有本次设备的执行证据
        assert "run_evidence_missing" in _report_codes(report)

    def test_run_timeout_is_reported(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()], run_statuses=["RUNNING"])
        report = _run_verify(site, api, run_timeout=0.0)

        assert "run_timeout" in _report_codes(report)

    def test_watcher_event_is_evidence(self, site):
        api = FakeApi(
            hosts=[_live_host()],
            devices=[_verified_device()],
            run_events=[{"title": "Watcher started", "category": "system"}],
        )
        report = _run_verify(site, api)

        assert _report_status(report, "verify.s6.watcher") == "PASS"

    def test_missing_specialty_stops_the_chain(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()], specialties=[])
        report = _run_verify(site, api)

        assert "specialty_missing" in _report_codes(report)
        assert api.plans_created == []

    def test_script_scan_failure_is_reported(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()], scan_status=500)
        report = _run_verify(site, api)

        assert "script_scan_failed" in _report_codes(report)
        assert api.plans_created == []

    def test_rejected_plan_create_is_reported(self, site):
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()], plan_create=(400, {"error": {}}))
        report = _run_verify(site, api)

        assert "plan_create_failed" in _report_codes(report)

    def test_missing_admin_binding_stops_before_network(self, site, tmp_path):
        (tmp_path / "bindings/site_admin").unlink()
        api = FakeApi(hosts=[_live_host()], devices=[_verified_device()])
        report = _run_verify(site, api)

        assert "binding_file" in _report_codes(report)
        assert api.login_calls == []

    def test_rejected_admin_credentials_are_reported(self, site):
        report = _run_verify(site, FakeApi(auth_error=True))

        assert "api_auth" in _report_codes(report)

    def test_unreachable_entry_is_reported(self, site):
        report = _run_verify(site, FakeApi(unreachable=True))

        assert "api_unreachable" in _report_codes(report)

    def test_authorized_probe_writes_reads_and_cleans_up(self, site, monkeypatch):
        """显式授权的探针：写→读回→只删自己那个文件，其余一概不动。"""
        monkeypatch.setattr(os.path, "ismount", lambda path: True)
        share = Path(site().config.storage.mount_path)
        share.mkdir(parents=True)
        (share / "keep-me.txt").write_text("existing\n", encoding="utf-8")

        report = _run_verify(
            site, FakeApi(hosts=[_live_host()], devices=[_verified_device()]),
            storage_probe_subdir="probe",
        )

        assert _report_status(report, "verify.s6.storage") == "PASS"
        assert "storage_probe_ok" in _report_codes(report)
        assert (share / "keep-me.txt").read_text(encoding="utf-8") == "existing\n"
        assert list((share / "probe").iterdir()) == [], "探针留下了自己的文件"

    def test_probe_is_blocked_without_explicit_authorization(self, site):
        report = _run_verify(site, FakeApi(hosts=[_live_host()]))

        assert _report_status(report, "verify.s6.storage") == "BLOCKED"
        assert "storage_probe_not_authorized" in _report_codes(report)

    def test_probe_refuses_a_path_shaped_subdir(self, site):
        """探针输入不得越出挂载点：带斜杠或回退的取值一律拒绝，且不写任何东西。"""
        share = Path(site().config.storage.mount_path)
        report = _run_verify(
            site, FakeApi(hosts=[_live_host()]), storage_probe_subdir="../escape",
        )

        assert "storage_probe_subdir" in _report_codes(report)
        assert not share.exists(), "拒绝的取值仍然创建了目录"

    def test_probe_refuses_a_path_that_is_not_mounted(self, site, monkeypatch):
        """没挂上时绝不能写进本机同名目录——那正是"掩盖未挂载"的假通过。"""
        monkeypatch.setattr(os.path, "ismount", lambda path: False)
        share = Path(site().config.storage.mount_path)
        share.mkdir(parents=True)

        report = _run_verify(
            site, FakeApi(hosts=[_live_host()]), storage_probe_subdir="probe",
        )

        assert "shared_storage_not_mounted" in _report_codes(report)
        assert not (share / "probe").exists()

    def test_probe_reports_an_unwritable_share(self, site, monkeypatch):
        monkeypatch.setattr(os.path, "ismount", lambda path: True)
        share = Path(site().config.storage.mount_path)
        share.mkdir(parents=True)
        (share / "probe").write_text("a file where the probe needs a directory\n", encoding="utf-8")

        report = _run_verify(
            site, FakeApi(hosts=[_live_host()]), storage_probe_subdir="probe",
        )

        assert "storage_unwritable" in _report_codes(report)

    def test_report_never_contains_binding_values(self, site):
        report = _run_verify(site, FakeApi(hosts=[_live_host()], devices=[_verified_device()]))
        rendered = json.dumps(report, ensure_ascii=False)

        assert ADMIN_PASSWORD not in rendered
        assert AGENT_PASSWORD not in rendered
        assert PRIVATE_MARKER not in rendered

    def test_cli_exposes_verify_command(self, tmp_path, monkeypatch):
        """CLI 接线：verify 子命令把参数原样交给 verify_site。"""
        import tools.site_config.__main__ as cli
        import tools.site_config.verify as verify_module

        captured: dict = {}

        def fake_verify(config, **kwargs):
            captured.update({"config": config, **kwargs})
            return {"stage": "verify", "status": "PASS", "summary": "", "checks": []}

        # 只打真实来源模块：CLI 在分支内延迟导入 verify_site（preflight 要能在
        # 没有第三方依赖的机器上跑），所以 `cli.verify_site` 不再是打桩点。
        monkeypatch.setattr(verify_module, "verify_site", fake_verify)
        bindings = tmp_path / "b"
        bindings.mkdir(mode=0o700)
        code = cli.main([
            "verify", "--config", str(tmp_path / "site.yaml"),
            "--bindings-dir", str(bindings), "--device-serial", "SYNTH0001",
            "--run-timeout", "12", "--json",
            "--storage-probe-subdir", "probe",
        ])

        assert code == 0
        assert captured["device_serial"] == "SYNTH0001"
        assert captured["run_timeout"] == 12.0
        assert captured["storage_probe_subdir"] == "probe"

    def test_cli_text_report_does_not_crash(self, tmp_path, monkeypatch, capsys):
        """文本模式同样消费 verify 报告（校验 deferred_checks 等字段齐备）。"""
        import tools.site_config.__main__ as cli
        import tools.site_config.verify as verify_module

        monkeypatch.setattr(verify_module, "verify_site", lambda config, **kwargs: {
            "stage": "verify",
            "status": "PASS",
            "summary": "synthetic",
            "checks": [{
                "check_id": "verify.s6.auth", "role": "control_plane", "status": "PASS",
                "location": "$", "code": "admin_authenticated", "message": "ok", "remediation": "none",
            }],
            "deferred_checks": [],
        })
        bindings = tmp_path / "b2"
        bindings.mkdir(mode=0o700)

        code = cli.main(["verify", "--config", str(tmp_path / "site.yaml"), "--bindings-dir", str(bindings)])

        assert code == 0
        assert "verify.s6.auth" in capsys.readouterr().out
