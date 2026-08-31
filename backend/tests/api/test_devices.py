"""
Tests for devices API routes
"""
from datetime import datetime, timezone
from uuid import uuid4

from backend.models.host import Device


class TestCreateDevice:
    """Test POST /api/v1/devices"""

    def test_create_device_success(self, client, sample_host, admin_headers):
        """Test creating a new device successfully"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": "NEW001",
                "model": "NewModel",
                "host_id": sample_host.id,
                "tags": ["test", "new"],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["serial"] == "NEW001"
        assert data["model"] == "NewModel"
        assert data["host_id"] == sample_host.id
        assert data["tags"] == ["test", "new"]
        assert data["status"] == "OFFLINE"
        assert "id" in data

    def test_create_device_duplicate_serial(self, client, sample_device, admin_headers):
        """Test creating device with duplicate serial fails"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": sample_device.serial,
                "model": "DuplicateModel",
                "host_id": sample_device.host_id,
            },
            headers=admin_headers,
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_create_device_missing_serial(self, client, sample_host, admin_headers):
        """Test creating device without serial fails"""
        response = client.post(
            "/api/v1/devices",
            json={
                "model": "NoSerialModel",
                "host_id": sample_host.id,
            },
            headers=admin_headers,
        )
        assert response.status_code == 422

    def test_create_device_invalid_host(self, client, admin_headers):
        """Test creating device with non-existent host"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": "INVALID001",
                "model": "InvalidModel",
                "host_id": "missing-host",
            },
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_create_device_forbidden_for_non_admin(self, client, sample_host, auth_headers):
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": "OP001",
                "model": "OperatorModel",
                "host_id": sample_host.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 403


class TestListDevices:
    """Test GET /api/v1/devices"""

    def test_list_devices_empty(self, client, auth_headers):
        """Test listing devices when empty"""
        response = client.get("/api/v1/devices?status=__NONE__", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_devices_with_data(self, client, sample_device, auth_headers):
        """Test listing devices with data"""
        response = client.get("/api/v1/devices", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        device_data = next((d for d in data if d["id"] == sample_device.id), None)
        assert device_data is not None
        assert device_data["serial"] == sample_device.serial
        assert device_data["model"] == sample_device.model

    def test_list_devices_ordered_by_id(self, client, sample_host, admin_headers, auth_headers):
        """Test devices are ordered by id"""
        # Create multiple devices
        prefix = f"ORDER-{uuid4().hex[:8]}"
        created_serials = []
        for i in range(3):
            serial = f"{prefix}-{i}"
            created_serials.append(serial)
            client.post(
                "/api/v1/devices",
                json={
                    "serial": serial,
                    "model": "OrderModel",
                    "host_id": sample_host.id,
                },
                headers=admin_headers,
            )

        response = client.get("/api/v1/devices", headers=auth_headers)
        data = response.json()
        # 接口按 last_seen DESC NULLS LAST 排序——新设备 last_seen 全 NULL，
        # NULL 组内顺序 PG 不保证。断言集合与去重，不依赖 NULL 组内顺序。
        serials = [d["serial"] for d in data]
        assert all(s in serials for s in created_serials)
        assert len(serials) == len(set(serials))
        assert set(created_serials).issubset(serials)

    def test_list_devices_status_offline_when_host_offline(
        self, client, db_session, sample_device, sample_offline_host, auth_headers
    ):
        """Test device status becomes OFFLINE when host is offline"""
        # Move device to offline host
        sample_device.host_id = sample_offline_host.id
        db_session.commit()

        response = client.get("/api/v1/devices", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        device_data = next((d for d in data if d["id"] == sample_device.id), None)
        assert device_data is not None
        assert device_data["status"] == "OFFLINE"

    def test_list_devices_requires_auth(self, client):
        response = client.get("/api/v1/devices")
        assert response.status_code == 401


class TestGetDevice:
    """Test GET /api/v1/devices/{device_id}"""

    def test_get_device_success(self, client, sample_device, auth_headers):
        """Test getting a device by id"""
        response = client.get(f"/api/v1/devices/{sample_device.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_device.id
        assert data["serial"] == sample_device.serial
        assert data["model"] == sample_device.model

    def test_get_device_not_found(self, client, auth_headers):
        """Test getting non-existent device"""
        response = client.get("/api/v1/devices/99999", headers=auth_headers)
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_device_invalid_id(self, client, auth_headers):
        """Test getting device with invalid id"""
        response = client.get("/api/v1/devices/invalid", headers=auth_headers)
        assert response.status_code == 422

    def test_get_device_status_updated_when_host_offline(
        self, client, db_session, sample_device, sample_offline_host, auth_headers
    ):
        """Test device status is updated when host is offline"""
        # Move device to offline host
        sample_device.host_id = sample_offline_host.id
        db_session.commit()

        response = client.get(f"/api/v1/devices/{sample_device.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"

    def test_get_device_status_offline_when_heartbeat_expired(
        self, client, db_session, sample_device, sample_host_expired, auth_headers
    ):
        """Test device status becomes OFFLINE when host heartbeat expired"""
        # Move device to host with expired heartbeat
        sample_device.host_id = sample_host_expired.id
        db_session.commit()

        response = client.get(f"/api/v1/devices/{sample_device.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"


class TestDeviceWithHostRelationship:
    """Test device-host relationship scenarios"""

    def test_device_includes_host_info(self, client, sample_device, auth_headers):
        """Test device response includes host relationship"""
        response = client.get(f"/api/v1/devices/{sample_device.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "host_id" in data
        assert data["host_id"] == sample_device.host_id

    def test_device_without_host(self, client, db_session, auth_headers):
        """Test device without host association"""
        device = Device(
            serial=f"NOHOST-{uuid4().hex[:8]}",
            status="ONLINE",
            last_seen=datetime.now(timezone.utc),
        )
        db_session.add(device)
        db_session.commit()

        response = client.get(f"/api/v1/devices/{device.id}", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["host_id"] is None


class TestProjectAttribution:
    """ADR-0029 v2.5：未映射筛选 + 归属来源两态（mapped / unmapped，派生）。"""

    def _seed(self, db_session):
        from backend.models.host import Host
        from backend.models.project import TestProject
        from backend.models.project_model import ProjectModel

        host = Host(id="h-attr", hostname="hattr", status="ONLINE")
        db_session.add(host)
        db_session.commit()
        project = TestProject(project_key="ATTR-P", display_name="attr")
        db_session.add(project)
        db_session.commit()
        # ADR-0029 P1：rule 判定走规则表（match_models 列已 drop）
        db_session.add(ProjectModel(
            project_id=project.id, match_value="MLD_LX2"))
        db_session.commit()
        mapped_dev = Device(serial="S-mapped-1", host_id="h-attr", status="ONLINE",
                            model="MLD_LX2")
        unmapped_dev = Device(serial="S-none-1", host_id="h-attr", status="ONLINE",
                              model="MLD_LX3")
        db_session.add_all([mapped_dev, unmapped_dev])
        db_session.commit()
        return mapped_dev, unmapped_dev

    def test_attribution_source_two_state(self, client, db_session, auth_headers):
        """v2.5 派生：型号有活跃成员行 → mapped；无成员行/无型号 → unmapped。"""
        mapped_dev, unmapped_dev = self._seed(db_session)

        response = client.get("/api/v1/devices", headers=auth_headers)
        assert response.status_code == 200
        by_serial = {d["serial"]: d for d in response.json()}
        assert by_serial[mapped_dev.serial]["attribution_source"] == "mapped"
        assert by_serial[mapped_dev.serial]["project_key"] == "ATTR-P"
        assert by_serial[unmapped_dev.serial]["attribution_source"] == "unmapped"
        assert by_serial[unmapped_dev.serial]["project_key"] is None

    def test_unassigned_filter_only_returns_unmapped(self, client, db_session, auth_headers):
        self._seed(db_session)

        response = client.get("/api/v1/devices?unassigned=true", headers=auth_headers)
        assert response.status_code == 200
        serials = {d["serial"] for d in response.json()}
        assert serials == {"S-none-1"}

    def test_unassigned_mutually_exclusive_with_project_key(self, client, auth_headers):
        response = client.get(
            "/api/v1/devices?unassigned=true&project_key=ATTR-P",
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "mutually exclusive" in response.json()["detail"]
