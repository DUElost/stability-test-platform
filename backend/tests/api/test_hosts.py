"""
Tests for hosts API routes
"""
from cryptography.fernet import Fernet
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest


class TestCreateHost:
    """Test POST /api/v1/hosts"""

    def test_create_host_success(self, client, admin_headers):
        """Test creating a new host successfully"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "new-host",
                "ip": "192.168.1.200",
                "ssh_port": 22,
                "ssh_user": "root",
                "ssh_auth_type": "password",
                "ssh_key_path": None,
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "new-host"
        assert data["ip"] == "192.168.1.200"
        assert data["ssh_port"] == 22
        assert data["ssh_user"] == "root"
        assert data["status"] == "OFFLINE"
        assert data["watcher_admin_active"] is True
        assert data["id"] == "192-168-1-200"

    def test_create_host_duplicate_name(self, client, sample_host, admin_headers):
        """Test creating host with duplicate name fails"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": sample_host.name,
                "ip": "192.168.1.203",
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert response.status_code == 409, response.text
        body = response.json()["detail"]
        assert body["code"] == "HOST_IDENTITY_CONFLICT"
        assert body["field"] == "name"
        assert body["conflicting_host_id"] == sample_host.id

    def test_create_host_duplicate_ip(self, client, sample_host, admin_headers):
        """#101: 同一 IP 不能登记两行——重复 IP 必须 409。"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "another-name",
                "ip": sample_host.ip,
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert response.status_code == 409, response.text
        body = response.json()["detail"]
        assert body["code"] == "HOST_IDENTITY_CONFLICT"
        assert body["field"] == "ip"
        assert body["conflicting_host_id"] == sample_host.id

    def test_update_host_ip_conflict(self, client, sample_host, sample_offline_host, admin_headers):
        """#101: update 把 ip 改成其他 host 已占用的值必须 409。"""
        response = client.put(
            f"/api/v1/hosts/{sample_host.id}",
            json={
                "name": sample_host.name,
                "ip": sample_offline_host.ip,
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert response.status_code == 409, response.text
        body = response.json()["detail"]
        assert body["code"] == "HOST_IDENTITY_CONFLICT"
        assert body["field"] == "ip"
        assert body["conflicting_host_id"] == sample_offline_host.id

    def test_create_host_missing_name(self, client, admin_headers):
        """Test creating host without name fails"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "ip": "192.168.1.202",
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_create_host_missing_ip(self, client, admin_headers):
        """Test creating host without IP fails"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "no-ip-host",
                "ssh_port": 22,
            },
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_create_host_default_ssh_port(self, client, admin_headers):
        """Test creating host with default SSH port"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "default-port-host",
                "ip": "192.168.1.203",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ssh_port"] == 22

    def test_create_host_with_key_auth(self, client, admin_headers):
        """Test creating host with key authentication"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "key-auth-host",
                "ip": "192.168.1.204",
                "ssh_port": 22,
                "ssh_user": "admin",
                "ssh_auth_type": "key",
                "ssh_key_path": "/path/to/key.pem",  # Accepted in input but not returned in output
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ssh_auth_type"] == "key"
        # ssh_key_path is intentionally excluded from output for security

    def test_create_host_encrypts_ssh_password(self, client, admin_headers, db_session, monkeypatch):
        from backend.models.host import Host

        monkeypatch.setenv("SSH_CREDENTIALS_FERNET_KEY", Fernet.generate_key().decode())
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "password-host",
                "ip": "192.168.1.206",
                "ssh_port": 22,
                "ssh_user": "root",
                "ssh_auth_type": "password",
                "ssh_password": "top-secret-password",
                "ssh_known_hosts_path": "/etc/stp/known_hosts",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "ssh_password" not in data
        assert "ssh_known_hosts_path" not in data

        host = db_session.get(Host, data["id"])
        assert host is not None
        assert getattr(host, "ssh_password_enc", "")
        assert host.ssh_password_enc != "top-secret-password"
        assert "ssh_password" not in (host.extra or {})

    def test_create_host_forbidden_for_non_admin(self, client, auth_headers):
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "forbidden-host",
                "ip": "192.168.1.205",
            },
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestListHosts:
    """Test GET /api/v1/hosts"""

    def test_list_hosts_empty(self, client, auth_headers):
        """Test listing hosts when empty"""
        response = client.get("/api/v1/hosts", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert all("id" in item for item in data)

    def test_list_hosts_with_data(self, client, sample_host, auth_headers):
        """Test listing hosts with data"""
        response = client.get("/api/v1/hosts", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        host_data = next((h for h in data if h["id"] == sample_host.id), None)
        assert host_data is not None
        assert host_data["name"] == sample_host.name
        assert host_data["ip"] == sample_host.ip

    def test_list_hosts_ordered_by_id(self, client, admin_headers, auth_headers):
        """Test hosts are ordered by id"""
        # Create multiple hosts
        created_names = []
        for i in range(3):
            name = f"order-host-{i}-{uuid4().hex[:8]}"
            created_names.append(name)
            client.post(
                "/api/v1/hosts",
                json={
                    "name": name,
                    "ip": f"192.168.1.{210 + i}",
                },
                headers=admin_headers,
            )

        response = client.get("/api/v1/hosts", headers=auth_headers)
        data = response.json()
        ids = [d["id"] for d in data]
        assert ids == sorted(ids)
        names = {d["name"] for d in data}
        assert set(created_names).issubset(names)

    def test_list_hosts_status_updated_on_expired_heartbeat(
        self, client, sample_host_expired, auth_headers
    ):
        """Test host status is updated to OFFLINE when heartbeat expired"""
        response = client.get("/api/v1/hosts", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        host_data = next(h for h in data if h["id"] == sample_host_expired.id)
        assert host_data["status"] == "OFFLINE"

    def test_list_hosts_requires_auth(self, client):
        response = client.get("/api/v1/hosts")
        assert response.status_code == 401


class TestGetHost:
    """Test GET /api/v1/hosts/{host_id}"""

    def test_get_host_success(self, client, sample_host, auth_headers):
        """Test getting a host by id"""
        response = client.get(f"/api/v1/hosts/{sample_host.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_host.id
        assert data["name"] == sample_host.name
        assert data["ip"] == sample_host.ip

    def test_get_host_not_found(self, client, auth_headers):
        """Test getting non-existent host"""
        response = client.get("/api/v1/hosts/99999", headers=auth_headers)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_host_invalid_id(self, client, auth_headers):
        """Test getting host with invalid id"""
        response = client.get("/api/v1/hosts/invalid", headers=auth_headers)
        assert response.status_code == 404

    def test_get_host_status_offline_when_heartbeat_expired(
        self, client, sample_host_expired, auth_headers
    ):
        """Test host status becomes OFFLINE when heartbeat expired"""
        response = client.get(
            f"/api/v1/hosts/{sample_host_expired.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"


class TestHostStatusTransitions:
    """Test host status transition logic"""

    def test_host_status_not_changed_if_already_offline(
        self, client, sample_offline_host, auth_headers
    ):
        """Test host status is not changed if already offline"""
        response = client.get(
            f"/api/v1/hosts/{sample_offline_host.id}",
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"

    def test_host_with_recent_heartbeat_stays_online(self, client, sample_host, auth_headers):
        """Test host with recent heartbeat stays online"""
        sample_host.last_heartbeat = datetime.now(timezone.utc)
        response = client.get(f"/api/v1/hosts/{sample_host.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ONLINE"

    def test_host_status_updated_in_list_view(self, client, sample_host_expired, auth_headers):
        """Test host status is updated in list view when heartbeat expired"""
        response = client.get("/api/v1/hosts", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        host_data = next(h for h in data if h["id"] == sample_host_expired.id)
        assert host_data["status"] == "OFFLINE"


class TestHostFields:
    """Test host field validation and responses"""

    def test_host_response_includes_all_fields(self, client, sample_host, auth_headers):
        """Test host response includes all expected fields"""
        response = client.get(f"/api/v1/hosts/{sample_host.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        expected_fields = [
            "id", "name", "ip", "ssh_port", "ssh_user",
            "status", "last_heartbeat", "extra", "mount_status"
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_host_extra_field_defaults_to_empty_dict(self, client, admin_headers):
        """Test host extra field defaults to empty dict"""
        response = client.post(
            "/api/v1/hosts",
            json={
                "name": "test-extra-host",
                "ip": "192.168.1.220",
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["extra"] == {}
        assert data["mount_status"] == {}
        assert data["watcher_admin_active"] is True

    def test_host_extra_redacts_sensitive_values(self, client, db_session, auth_headers):
        from backend.models.host import Host

        host = Host(
            id="sensitive-host",
            hostname="sensitive-host",
            name="sensitive-host",
            ip="192.168.1.240",
            ip_address="192.168.1.240",
            extra={"ssh_password": "top-secret", "ssh_key_path": "/tmp/key", "rack": "A1"},
            status="ONLINE",
            last_heartbeat=datetime.now(timezone.utc),
        )
        db_session.add(host)
        db_session.commit()

        response = client.get("/api/v1/hosts/sensitive-host", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["extra"] == {"rack": "A1"}


class TestWatcherAdminState:
    def test_host_response_includes_watcher_admin_active(
        self, client, sample_host, auth_headers
    ):
        response = client.get(f"/api/v1/hosts/{sample_host.id}", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["watcher_admin_active"] is True

    def test_patch_watcher_admin_state_success(
        self, client, sample_host, admin_headers, db_session
    ):
        response = client.patch(
            f"/api/v1/hosts/{sample_host.id}/watcher-admin-state",
            json={"watcher_admin_active": False},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["id"] == sample_host.id
        assert data["watcher_admin_active"] is False

        db_session.refresh(sample_host)
        assert sample_host.watcher_admin_active is False

    def test_patch_watcher_admin_state_forbidden_for_non_admin(
        self, client, sample_host, auth_headers
    ):
        response = client.patch(
            f"/api/v1/hosts/{sample_host.id}/watcher-admin-state",
            json={"watcher_admin_active": False},
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestUpdateHostPreserveSsh:
    """#950: PUT /hosts/{id} 只写提交字段——未提交的密钥认证配置必须保留。"""

    def _create_key_host(self, client, admin_headers):
        created = client.post(
            "/api/v1/hosts",
            json={
                "name": "key-host",
                "ip": "192.168.50.77",
                "ssh_port": 22,
                "ssh_user": "ops",
                "ssh_auth_type": "key",
                "ssh_key_path": "/secret/key.pem",
            },
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        return created.json()["id"]

    def test_update_name_only_keeps_key_auth_config(
        self, client, db_session, admin_headers,
    ):
        """仅改名称（编辑表单真实形态：不提交认证字段）→ key 配置保留。"""
        from backend.models.host import Host

        host_id = self._create_key_host(client, admin_headers)

        resp = client.put(
            f"/api/v1/hosts/{host_id}",
            json={"name": "key-host-renamed"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "key-host-renamed"
        assert data["ssh_auth_type"] == "key"  # 未被重置为 password

        host = db_session.get(Host, host_id)
        assert host.ssh_auth_type == "key"
        assert host.ssh_key_path == "/secret/key.pem"  # output 隐藏但 DB 保留
        assert host.ssh_user == "ops"

    def test_explicit_auth_change_still_updates(
        self, client, db_session, admin_headers,
    ):
        """显式提交 ssh_auth_type 切换 → 正常更新；同请求未提交的
        ssh_known_hosts_path 保留。"""
        from backend.models.host import Host

        host_id = self._create_key_host(client, admin_headers)
        db_session.query(Host).filter(Host.id == host_id).update(
            {"ssh_known_hosts_path": "/etc/stp/known_hosts"}
        )
        db_session.commit()

        resp = client.put(
            f"/api/v1/hosts/{host_id}",
            json={"ssh_auth_type": "password", "ssh_key_path": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ssh_auth_type"] == "password"

        host = db_session.get(Host, host_id)
        assert host.ssh_key_path is None
        assert host.ssh_known_hosts_path == "/etc/stp/known_hosts"  # 未提交保留

    def test_ip_change_still_scans_host_key(
        self, client, db_session, admin_headers,
    ):
        """回归：IP 实际变化仍触发 host key 重扫（依赖 host.ip 比较）。"""
        host_id = self._create_key_host(client, admin_headers)

        resp = client.put(
            f"/api/v1/hosts/{host_id}",
            json={"ip": "192.168.50.78"},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ip"] == "192.168.50.78"


# ── #908：主机密钥变更默认拒绝静默替换，显式换钥可审计 ─────────────────────


class TestHostKeyReplaceConsent:
    def _fake_keyscan(self, monkeypatch, key_blob: str):
        from types import SimpleNamespace
        from backend.core import ssh_security

        monkeypatch.setattr(
            ssh_security.subprocess, "run",
            lambda *a, **k: SimpleNamespace(
                returncode=0,
                stdout=f"10.9.9.9 ssh-ed25519 {key_blob}\n",
                stderr="",
            ),
        )

    def test_create_refuses_silent_replace(self, client, admin_headers, tmp_path, monkeypatch):
        """known_hosts 已有不同密钥且未确认 → host_key_trust=changed，文件不动。"""
        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("10.9.9.9 ssh-ed25519 T0xES0VZ\n", encoding="utf-8")
        self._fake_keyscan(monkeypatch, "TkVXS0VZ")

        resp = client.post("/api/v1/hosts", json={
            "name": "h908a", "ip": "10.9.9.9", "ssh_port": 22,
            "ssh_known_hosts_path": str(known_hosts),
        }, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["host_key_trust"] == "changed"
        assert "T0xES0VZ" in known_hosts.read_text(encoding="utf-8"), "未确认不得覆盖"

    def test_create_with_explicit_replace_is_audited(
        self, client, admin_headers, tmp_path, monkeypatch, db_session,
    ):
        """replace_host_key=true → 替换 + 审计 host_key_replaced（含指纹）。"""
        from backend.models.audit import AuditLog

        known_hosts = tmp_path / "known_hosts"
        known_hosts.write_text("10.9.9.9 ssh-ed25519 T0xES0VZ\n", encoding="utf-8")
        self._fake_keyscan(monkeypatch, "TkVXS0VZ")

        resp = client.post("/api/v1/hosts", json={
            "name": "h908b", "ip": "10.9.9.9", "ssh_port": 22,
            "ssh_known_hosts_path": str(known_hosts),
            "replace_host_key": True,
        }, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json()["host_key_trust"] == "ok"
        content = known_hosts.read_text(encoding="utf-8")
        assert "T0xES0VZ" not in content and "TkVXS0VZ" in content

        audit = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "host_key_replaced")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit is not None, "显式换钥必须留下审计"
        details = audit.details or {}
        assert "SHA256:" in str(details.get("change", ""))


class TestKnownHostsPathValidation:
    """code-scanning #78：known_hosts 落点在入库前收敛为绝对路径或 ``~/`` 前缀。

    该值最终是控制面的 mkdir/touch/重写目标（换钥）与 SSH 读取目标，因此相对
    路径、``..`` 段与他人 home（``~user/``）必须在 API 边界就拒绝，而不是等到
    换钥阶段静默失败。
    """

    @pytest.fixture(autouse=True)
    def _keyscan_returns_nothing(self, monkeypatch):
        """换钥扫描与本契约无关：给一个「扫不到键」的假 ssh-keyscan，避免真外连。"""
        from types import SimpleNamespace

        from backend.core import ssh_security

        monkeypatch.setattr(
            ssh_security.subprocess, "run",
            lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
        )

    def test_create_accepts_and_normalizes_absolute_path(
        self, client, admin_headers, db_session,
    ):
        from backend.models.host import Host

        resp = client.post("/api/v1/hosts", json={
            "name": "kh-ok", "ip": "10.9.9.11", "ssh_port": 22,
            "ssh_known_hosts_path": "/etc//stp/known_hosts",
        }, headers=admin_headers)
        assert resp.status_code == 200, resp.text

        host = db_session.query(Host).filter(Host.name == "kh-ok").one()
        assert host.ssh_known_hosts_path == "/etc/stp/known_hosts", (
            "入库值必须是归一化后的实际落点，而非提交原文"
        )

    def test_create_rejects_relative_path(self, client, admin_headers):
        resp = client.post("/api/v1/hosts", json={
            "name": "kh-relative", "ip": "10.9.9.12", "ssh_port": 22,
            "ssh_known_hosts_path": "etc/stp/known_hosts",
        }, headers=admin_headers)
        assert resp.status_code == 422, resp.text
        assert "known_hosts path must be absolute" in resp.text

    def test_update_rejects_parent_traversal(
        self, client, admin_headers, db_session,
    ):
        from backend.models.host import Host

        created = client.post(
            "/api/v1/hosts",
            json={"name": "kh-traverse", "ip": "10.9.9.13", "ssh_port": 22},
            headers=admin_headers,
        )
        assert created.status_code == 200, created.text
        host_id = created.json()["id"]

        resp = client.put(
            f"/api/v1/hosts/{host_id}",
            json={"ssh_known_hosts_path": "/etc/stp/../../tmp/kh"},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "must not contain '..'" in resp.text
        assert db_session.get(Host, host_id).ssh_known_hosts_path is None, (
            "被拒的请求不得留下部分写入"
        )


class TestHostHardDeleteGuards:
    """#937/#796: 有历史依赖的主机硬删除返回 409（不裸 500/不静默清空）。"""

    def test_delete_host_with_devices_is_409(
        self, client, db_session, admin_headers,
    ):
        from backend.models.host import Device, Host

        db_session.add(Host(id="del-h-dev", hostname="dhd", status="OFFLINE"))
        db_session.commit()
        db_session.add(Device(
            serial="del-dev-1", host_id="del-h-dev", status="OFFLINE",
        ))
        db_session.commit()

        resp = client.delete("/api/v1/hosts/del-h-dev", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert "设备" in resp.json()["detail"]

    def test_delete_clean_host_succeeds(
        self, client, db_session, admin_headers,
    ):
        from backend.models.host import Host

        db_session.add(Host(id="del-h-clean", hostname="dhc", status="OFFLINE"))
        db_session.commit()

        resp = client.delete("/api/v1/hosts/del-h-clean", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert db_session.get(Host, "del-h-clean") is None

    def test_delete_host_with_job_history_is_409_and_preserves_history(
        self, client, db_session, admin_headers,
    ):
        """#796: 历史 Job 即数据——409 且 job/device 行不得被 CASCADE 清空。"""
        from backend.models.enums import JobStatus
        from backend.models.host import Device, Host
        from backend.models.job import JobInstance
        from backend.models.plan import Plan
        from backend.models.plan_run import PlanRun

        host = Host(id="del-h-job", hostname="dhj", status="OFFLINE")
        plan = Plan(name="del-h-job-plan")
        db_session.add_all([host, plan])
        db_session.flush()
        device = Device(
            serial="del-dev-job", host_id=host.id, status="OFFLINE",
        )
        db_session.add(device)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id, status="SUCCESS",
            plan_snapshot={"name": plan.name}, run_type="MANUAL",
        )
        db_session.add(run)
        db_session.flush()
        job = JobInstance(
            plan_run_id=run.id, plan_id=plan.id, device_id=device.id,
            host_id=host.id, status=JobStatus.COMPLETED.value,
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        )
        db_session.add(job)
        db_session.commit()
        job_id, device_id = job.id, device.id

        resp = client.delete("/api/v1/hosts/del-h-job", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert "历史 Job" in resp.json()["detail"]

        # 409 是数据保护而非部分删除：job/device/host 行必须原样保留
        db_session.expire_all()
        assert db_session.get(JobInstance, job_id) is not None
        assert db_session.get(Device, device_id) is not None
        assert db_session.get(Host, "del-h-job") is not None

    def test_delete_host_with_plan_run_projection_is_409(
        self, client, db_session, admin_headers,
    ):
        """#796: 仅剩 plan_run_host 投影（无 job/device）也不得硬删。"""
        from backend.models.host import Host
        from backend.models.plan import Plan
        from backend.models.plan_run import PlanRun, PlanRunHost

        db_session.add(Host(id="del-h-prh", hostname="dhp", status="OFFLINE"))
        plan = Plan(name="del-h-prh-plan")
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id, status="SUCCESS",
            plan_snapshot={}, run_type="MANUAL",
        )
        db_session.add(run)
        db_session.flush()
        db_session.add(PlanRunHost(plan_run_id=run.id, host_id="del-h-prh"))
        db_session.commit()

        resp = client.delete("/api/v1/hosts/del-h-prh", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert "投影" in resp.json()["detail"]


class TestRetiredHostControlPlaneRejects:
    """#1805 切片二 / ADR-0038 D5：退役主机拒绝执行/配置类动作。

    覆盖：hot-update / install / watcher-admin-state 三处路由级拒绝；
    reload-config 在 dedup 路由测试文件覆盖。活体退役（ONLINE + retired_at）
    是 D4 明许形态，因此这里用 ONLINE 主机验证「status 不构成豁免」。
    """

    @staticmethod
    def _retired_online_host(db_session, host_id: str):
        from datetime import datetime, timezone

        from backend.models.host import Host

        host = Host(
            id=host_id, hostname=host_id, status="ONLINE",
            ip="192.0.2.55", ssh_port=22,
            retired_at=datetime.now(timezone.utc),
        )
        db_session.add(host)
        db_session.commit()
        return host

    def test_hot_update_retired_is_409(self, client, db_session, admin_headers):
        self._retired_online_host(db_session, "ret-hot")
        resp = client.post(
            "/api/v1/hosts/ret-hot/hot-update", headers=admin_headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "HOST_RETIRED"

    def test_install_retired_is_409(self, client, db_session, admin_headers, monkeypatch):
        import shutil

        # 依赖探测在退役判据之前——探测通过后才会走到 409（否则 501 抢先）
        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
        self._retired_online_host(db_session, "ret-install")
        resp = client.post(
            "/api/v1/hosts/ret-install/install", headers=admin_headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "HOST_RETIRED"

    def test_watcher_admin_state_retired_is_409(self, client, db_session, admin_headers):
        self._retired_online_host(db_session, "ret-watcher")
        resp = client.patch(
            "/api/v1/hosts/ret-watcher/watcher-admin-state",
            json={"watcher_admin_active": False},
            headers=admin_headers,
        )
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "HOST_RETIRED"


class TestHostArtifactDigestExposure:
    """ADR-0040 身份必须经 HostOut 暴露：站点侧内容一致性断言读的就是它。

    此前只在 heartbeat 入参/DB 列里存在，API 读不到 → 站点工具只能报
    agent_digest_missing（I4 容器实验室实测）。
    """

    def test_heartbeat_digests_are_visible_on_host(self, client, db_session, admin_headers):
        from backend.models.host import Host

        db_session.add(Host(
            id="digest-host", hostname="digest-host", name="digest-host",
            ip="192.0.2.44", ssh_port=22, status="ONLINE",
        ))
        db_session.commit()
        code = "sha256:" + "a" * 64
        resources = "sha256:" + "b" * 64

        beat = client.post("/api/v1/heartbeat", json={
            "host_id": "digest-host", "status": "ONLINE",
            "agent_artifact_digest": code, "agent_resources_digest": resources,
            "agent_instance_id": "inst-1", "boot_id": "boot-1",
        })
        assert beat.status_code == 200, beat.text

        resp = client.get("/api/v1/hosts/digest-host", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["agent_artifact_digest"] == code
        assert body["agent_resources_digest"] == resources


class TestHostInstallEndpoint:
    """I4：控制面驱动 Agent 安装——启动前校验配置/参数，并把选项下传执行链。"""

    @staticmethod
    def _online_host(db_session, host_id: str, ip: str = "192.0.2.77"):
        from backend.models.host import Host

        host = Host(id=host_id, hostname=host_id, status="ONLINE", ip=ip, ssh_port=22)
        db_session.add(host)
        db_session.commit()
        return host

    @pytest.fixture(autouse=True)
    def ansible_available(self, monkeypatch):
        """依赖探测在配置校验之前，测试里假定控制面已装 ansible-core/sshpass。"""
        import shutil

        monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")

    def test_install_without_api_url_is_400(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """STP_AGENT_INSTALL_API_URL 缺失 → 400，且不启动 ansible（无半个运行）。"""
        monkeypatch.delenv("STP_AGENT_INSTALL_API_URL", raising=False)
        self._online_host(db_session, "inst-no-url")
        resp = client.post("/api/v1/hosts/inst-no-url/install", headers=admin_headers)
        assert resp.status_code == 400, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "AGENT_INSTALL_NOT_CONFIGURED"
        assert "STP_AGENT_INSTALL_API_URL" in detail["message"]

    def test_install_with_non_origin_api_url_is_400(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        monkeypatch.setenv("STP_AGENT_INSTALL_API_URL", "https://stp.example.com/prefix")
        self._online_host(db_session, "inst-bad-url")
        resp = client.post("/api/v1/hosts/inst-bad-url/install", headers=admin_headers)
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"]["code"] == "AGENT_INSTALL_NOT_CONFIGURED"

    def test_install_passes_options_and_audits(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """install_options 下传执行链 + 审计记录回连地址与选项。"""
        import backend.api.routes.hosts as hosts_route

        monkeypatch.setenv("STP_AGENT_INSTALL_API_URL", "https://stp.example.com")
        started = {"ok": True, "console_run_id": "con-inst-1", "room": "console:con-inst-1"}
        start_mock = MagicMock(return_value=started)
        monkeypatch.setattr(hosts_route, "start_install_agent_runconsole", start_mock)
        self._online_host(db_session, "inst-opts")

        resp = client.post(
            "/api/v1/hosts/inst-opts/install",
            headers=admin_headers,
            json={
                "install_options": {
                    "agent_install_root": "/srv/stability-test-agent",
                    "agent_local_aee_root": "/mnt/hdd/aee_events",
                }
            },
        )
        assert resp.status_code == 200, resp.text
        assert start_mock.call_args.kwargs["install_options"] == {
            "agent_install_root": "/srv/stability-test-agent",
            "agent_local_aee_root": "/mnt/hdd/aee_events",
        }

        from backend.models.audit import AuditLog

        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "install_agent_request", AuditLog.resource_id == "inst-opts")
            .one()
        )
        assert row.details["agent_api_url"] == "https://stp.example.com"
        assert row.details["install_options"]["agent_install_root"] == "/srv/stability-test-agent"
        assert row.details["console_run_id"] == "con-inst-1"
        # ADR-0044：安装由 RunConsole 自持——响应不再有 saq_key；持久证据是审计
        # （host.extra 会被心跳按 allowlist 重建，不能承载控制面侧状态）。
        body = resp.json()
        assert "saq_key" not in body
        assert body["log_path"]

    def test_install_without_body_still_works(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """无请求体（旧调用方式）与空 install_options 等价，不因缺字段 422。"""
        import backend.api.routes.hosts as hosts_route

        monkeypatch.setenv("STP_AGENT_INSTALL_API_URL", "https://stp.example.com")
        start_mock = MagicMock(
            return_value={"ok": True, "console_run_id": "con-inst-2", "room": "console:con-inst-2"}
        )
        monkeypatch.setattr(hosts_route, "start_install_agent_runconsole", start_mock)
        self._online_host(db_session, "inst-nobody")

        resp = client.post("/api/v1/hosts/inst-nobody/install", headers=admin_headers)
        assert resp.status_code == 200, resp.text
        assert start_mock.call_args.kwargs["install_options"] is None
        assert "saq_key" not in resp.json()

    def test_install_duplicate_trigger_is_409(
        self, client, db_session, admin_headers, monkeypatch,
    ):
        """已在安装中的主机重复触发 → 409 且带 console_run_id（供前端接回日志）。"""
        import backend.api.routes.hosts as hosts_route

        monkeypatch.setenv("STP_AGENT_INSTALL_API_URL", "https://stp.example.com")
        monkeypatch.setattr(
            hosts_route,
            "start_install_agent_runconsole",
            MagicMock(return_value={"ok": False, "message": "install already in progress",
                                    "console_run_id": "con-running"}),
        )
        self._online_host(db_session, "inst-busy")

        resp = client.post("/api/v1/hosts/inst-busy/install", headers=admin_headers)
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["console_run_id"] == "con-running"


class TestHostInstallStatusEndpoint:
    """ADR-0044 D3/D4：安装状态以 RunConsole + 审计为唯一来源，重启后仍可回放。

    持久证据用的是 audit_logs（append-only）而不是 host.extra——后者会被心跳按
    allowlist 重建，控制面侧裸键会在 ~20 秒内被静默抹掉（238 现场实测）。
    """

    @staticmethod
    def _online_host(db_session, host_id: str, ip: str = "192.0.2.78"):
        from backend.models.host import Host

        host = Host(id=host_id, hostname=host_id, status="ONLINE", ip=ip, ssh_port=22)
        db_session.add(host)
        db_session.commit()
        return host

    @staticmethod
    def _audit(db_session, action: str, host_id: str, details: dict, *, at: datetime):
        from backend.models.audit import AuditLog

        row = AuditLog(
            action=action, resource_type="host", resource_id=host_id,
            details=details, timestamp=at,
        )
        db_session.add(row)
        db_session.commit()
        return row

    def test_live_run_is_reported_from_the_console(self, client, db_session, admin_headers, monkeypatch):
        import backend.api.routes.hosts as hosts_route

        self._online_host(db_session, "st-live")
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: "con-live")
        monkeypatch.setattr(
            hosts_route,
            "install_outcome_snapshot",
            lambda run_id: {
                "found": True,
                "status": "RUNNING",
                "exit_code": None,
                "log_path": "/var/log/stp/con-live.log",
            },
        )

        resp = client.get("/api/v1/hosts/st-live/install/status", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "running"
        assert body["console_status"] == "RUNNING"
        assert body["console_found"] is True
        assert body["room"] == "console:con-live"
        assert body["log_path"] == "/var/log/stp/con-live.log"
        assert "saq_key" not in body  # ADR-0044：SAQ 状态面已移除

    def test_finished_run_is_replayed_from_the_audit_log(self, client, db_session, admin_headers, monkeypatch):
        """安装结束后活动登记就清了——结果从审计回放（心跳不会改写审计）。"""
        import backend.api.routes.hosts as hosts_route

        self._online_host(db_session, "st-done")
        now = datetime.now(timezone.utc)
        self._audit(db_session, "install_agent_request", "st-done",
                    {"console_run_id": "con-done"}, at=now - timedelta(minutes=10))
        self._audit(db_session, "install_agent", "st-done",
                    {"ok": True, "rc": 0, "console_status": "SUCCESS", "console_run_id": "con-done",
                     "log_path": "/var/log/stp/con-done.log"}, at=now - timedelta(minutes=5))
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: None)

        resp = client.get("/api/v1/hosts/st-done/install/status", headers=admin_headers)

        body = resp.json()
        assert body["status"] == "succeeded"
        assert body["console_status"] == "SUCCESS"
        assert body["console_found"] is False
        assert body["console_run_id"] == "con-done"
        assert body["exit_code"] == 0
        assert body["log_path"] == "/var/log/stp/con-done.log"

    def test_started_run_without_outcome_is_lost_not_idle(self, client, db_session, admin_headers, monkeypatch):
        """有请求、没有更新的结果 = lost（控制面重启/结果未及落库），不能报成 idle。"""
        import backend.api.routes.hosts as hosts_route

        self._online_host(db_session, "st-lost")
        now = datetime.now(timezone.utc)
        self._audit(db_session, "install_agent", "st-lost",
                    {"ok": True, "console_status": "SUCCESS", "console_run_id": "con-old"},
                    at=now - timedelta(hours=2))
        self._audit(db_session, "install_agent_request", "st-lost",
                    {"console_run_id": "con-lost"}, at=now - timedelta(minutes=5))
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: None)

        resp = client.get("/api/v1/hosts/st-lost/install/status", headers=admin_headers)

        body = resp.json()
        assert body["status"] == "lost"
        assert body["console_status"] is None
        assert body["console_run_id"] == "con-lost"

    def test_host_without_any_install_is_idle(self, client, db_session, admin_headers, monkeypatch):
        import backend.api.routes.hosts as hosts_route

        self._online_host(db_session, "st-idle")
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: None)

        resp = client.get("/api/v1/hosts/st-idle/install/status", headers=admin_headers)

        body = resp.json()
        assert body["status"] == "idle"
        assert body["console_run_id"] is None
        assert body["log_path"] is None


class TestHostInstallCancelEndpoint:
    """#2255：安装控制台的取消入口——现场卡住时不必再重启控制面。

    口径与 dedup 的 cancel 一致：没有在跑的 run 也要落审计（取消是可归责动作）；
    取消 ≠ 失败——终态仍由 console 的 on_complete 写 `install_agent` 审计。
    """

    @staticmethod
    def _online_host(db_session, host_id: str, ip: str = "192.0.2.79"):
        from backend.models.host import Host

        host = Host(id=host_id, hostname=host_id, status="ONLINE", ip=ip, ssh_port=22)
        db_session.add(host)
        db_session.commit()
        return host

    def test_cancel_without_a_run_is_409_and_audited(self, client, db_session, admin_headers, monkeypatch):
        import backend.api.routes.hosts as hosts_route
        from backend.models.audit import AuditLog

        self._online_host(db_session, "cx-idle")
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: None)

        resp = client.post("/api/v1/hosts/cx-idle/install/cancel", headers=admin_headers)

        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["code"] == "NO_INSTALL_IN_PROGRESS"
        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "install_agent_cancel", AuditLog.resource_id == "cx-idle")
            .one()
        )
        assert row.details["reason"] == "no_install_in_progress"

    def test_cancel_reaches_the_console_and_is_audited(self, client, db_session, admin_headers, monkeypatch):
        import backend.api.routes.hosts as hosts_route
        from backend.models.audit import AuditLog
        from backend.services.run_console import RunConsole

        self._online_host(db_session, "cx-run")
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: "con-cx")
        cancel_mock = MagicMock(return_value=True)
        monkeypatch.setattr(RunConsole.instance(), "cancel", cancel_mock)

        resp = client.post("/api/v1/hosts/cx-run/install/cancel", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["canceled"] is True
        assert body["status"] == "canceling"
        assert body["console_run_id"] == "con-cx"
        cancel_mock.assert_called_once_with("con-cx")
        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "install_agent_cancel", AuditLog.resource_id == "cx-run")
            .one()
        )
        assert row.details["canceled"] is True
        assert row.details["console_run_id"] == "con-cx"

    def test_cancel_that_cannot_be_initiated_is_not_reported_as_done(self, client, db_session, admin_headers, monkeypatch):
        """已终态/跨实例不可达（console 返回 False）→ 如实报 not_canceled，不假装取消成功。"""
        import backend.api.routes.hosts as hosts_route
        from backend.models.audit import AuditLog
        from backend.services.run_console import RunConsole

        self._online_host(db_session, "cx-stale")
        monkeypatch.setattr(hosts_route, "get_active_install_console_id", lambda host_id: "con-stale")
        monkeypatch.setattr(RunConsole.instance(), "cancel", MagicMock(return_value=False))

        resp = client.post("/api/v1/hosts/cx-stale/install/cancel", headers=admin_headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["canceled"] is False
        assert body["status"] == "not_canceled"
        assert "could not be canceled" in body["message"]
        row = (
            db_session.query(AuditLog)
            .filter(AuditLog.action == "install_agent_cancel", AuditLog.resource_id == "cx-stale")
            .one()
        )
        assert row.details["reason"] == "cancel_not_initiated"


class TestHostsListDesiredDigestDegradation:
    """#2320：`GET /hosts` 现算 desired digest 失败时**降级**，不是 500。

    desired digest 是展示面判据（前端已按 `unknown` 渲染），一次 stat/open 失败只
    该让那一列变 unknown；旧实现里异常直接穿出路由，整张主机运维页不可用。
    """

    @pytest.fixture(autouse=True)
    def _reset_cooldown(self):
        from backend.api.routes import hosts as hosts_mod

        hosts_mod._desired_digest_failure_until = 0.0
        yield
        hosts_mod._desired_digest_failure_until = 0.0

    @staticmethod
    def _rows(payload):
        return payload if isinstance(payload, list) else payload["items"]

    def test_oserror_degrades_to_unknown_and_returns_200(
        self, client, auth_headers, db_session, sample_host, monkeypatch, caplog
    ):
        from backend.api.routes import hosts as hosts_mod

        sample_host.agent_artifact_digest = "sha256:agent-reported"
        db_session.commit()

        def _boom(**_kw):
            raise OSError("[Errno 116] Stale file handle")

        monkeypatch.setattr(hosts_mod, "compute_desired_artifact_digest", _boom)
        caplog.set_level("WARNING", logger="backend.api.routes.hosts")

        resp = client.get("/api/v1/hosts", headers=auth_headers)

        assert resp.status_code == 200, f"旧行为是 500：{resp.text[:200]}"
        rows = self._rows(resp.json())
        assert rows, "用例前提：至少一台主机"
        assert {r["agent_code_sync_status"] for r in rows} == {"unknown"}
        warned = [r for r in caplog.records if "hosts_desired_digest_failed" in r.getMessage()]
        assert len(warned) == 1, "降级必须可观测，且一次请求只报一次"

    def test_cooldown_prevents_per_request_retry(
        self, client, auth_headers, sample_host, monkeypatch
    ):
        """冷却：轮询热路径上不得每请求都重跑一遍注定失败的输入集遍历。"""
        from backend.api.routes import hosts as hosts_mod

        calls: list[int] = []

        def _boom(**_kw):
            calls.append(1)
            raise OSError("input set is moving")

        monkeypatch.setattr(hosts_mod, "compute_desired_artifact_digest", _boom)

        for _ in range(3):
            assert client.get("/api/v1/hosts", headers=auth_headers).status_code == 200

        assert len(calls) == 1, "第 2、3 次请求应走冷却，直接降级 unknown"

    def test_success_path_still_judges_sync_status(
        self, client, auth_headers, db_session, sample_host, monkeypatch
    ):
        """反向边界：正常路径的 digest 判据不得因加固而回归。"""
        from backend.api.routes import hosts as hosts_mod

        sample_host.agent_artifact_digest = "sha256:desired-value"
        db_session.commit()
        monkeypatch.setattr(
            hosts_mod,
            "compute_desired_artifact_digest",
            lambda **_kw: "sha256:desired-value",
        )

        resp = client.get("/api/v1/hosts", headers=auth_headers)

        assert resp.status_code == 200, resp.text
        rows = self._rows(resp.json())
        assert rows[0]["agent_code_sync_status"] == "matched"

    def test_programming_error_is_not_swallowed(
        self, client, auth_headers, sample_host, monkeypatch
    ):
        """只吃 OSError：`ValueError` 之类的真缺陷继续冒泡（不新增静默吞咽点，#739）。"""
        from backend.api.routes import hosts as hosts_mod

        def _bug(**_kw):
            raise ValueError("programming error, not an IO race")

        monkeypatch.setattr(hosts_mod, "compute_desired_artifact_digest", _bug)

        with pytest.raises(ValueError, match="programming error"):
            client.get("/api/v1/hosts", headers=auth_headers)
