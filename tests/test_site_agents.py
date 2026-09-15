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
        "install_root": str(tmp_path / f"opt/stp-agent-{key}"),
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
        return {
            "host_id": host_id,
            "status": "complete",
            "console_status": self._console_sequence[index],
        }

    def get_host(self, host_id: str) -> dict:
        self.host_reads += 1
        digest = None
        if self._digest_sequence:
            digest = self._digest_sequence[min(self.host_reads - 1, len(self._digest_sequence) - 1)]
        for host in self.hosts:
            if host["id"] == host_id:
                return {
                    **host,
                    "status": "ONLINE",
                    "last_heartbeat": _heartbeat(),
                    "agent_instance_id": "inst-1",
                    "boot_id": "boot-1",
                    **(
                        {"agent_artifact_digest": digest, "agent_resources_digest": ""}
                        if digest is not None
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
    return stage_s5_agents(ctx, api=api, **kwargs)


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
        api = FakeApi(digest_sequence=["", DIGEST_CODE])

        checks = _run(
            site(), api,
            digest_timeout=30.0,
            poll_interval=5.0,
            sleep=lambda seconds: slept.append(seconds),
        )

        assert api.host_reads >= 2, "未重读 Host 就下了结论"
        assert slept == [5.0]
        assert _status(checks, "install.s5.digest") == "PASS"

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

        monkeypatch.setattr(verify_module, "verify_site", fake_verify)
        monkeypatch.setattr(cli, "verify_site", fake_verify)
        bindings = tmp_path / "b"
        bindings.mkdir(mode=0o700)
        code = cli.main([
            "verify", "--config", str(tmp_path / "site.yaml"),
            "--bindings-dir", str(bindings), "--device-serial", "SYNTH0001",
            "--run-timeout", "12", "--json",
        ])

        assert code == 0
        assert captured["device_serial"] == "SYNTH0001"
        assert captured["run_timeout"] == 12.0

    def test_cli_text_report_does_not_crash(self, tmp_path, monkeypatch, capsys):
        """文本模式同样消费 verify 报告（校验 deferred_checks 等字段齐备）。"""
        import tools.site_config.__main__ as cli

        monkeypatch.setattr(cli, "verify_site", lambda config, **kwargs: {
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
