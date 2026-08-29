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
        ids = [d["id"] for d in data]
        assert ids == sorted(ids)
        serials = {d["serial"] for d in data}
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
    """ADR-0029 P0：未归属筛选 + 归属来源三态（rule / manual / unassigned）。"""

    def _seed(self, db_session):
        from backend.models.host import Host
        from backend.models.project import TestProject
        from backend.models.project_rule import ProjectDeviceRule

        host = Host(id="h-attr", hostname="hattr", status="ONLINE")
        db_session.add(host)
        db_session.commit()
        project = TestProject(project_key="ATTR-P", display_name="attr")
        db_session.add(project)
        db_session.commit()
        # ADR-0029 P1：rule 判定走规则表（match_models 列已 drop）
        db_session.add(ProjectDeviceRule(
            project_id=project.id, match_value="MLD_LX2"))
        db_session.commit()
        rule_dev = Device(serial="S-rule-1", host_id="h-attr", status="ONLINE",
                          model="MLD_LX2", project_id=project.id)
        manual_dev = Device(serial="S-manual-1", host_id="h-attr", status="ONLINE",
                            model="Z2581", project_id=project.id)
        pinned_dev = Device(serial="S-pinned-1", host_id="h-attr", status="ONLINE",
                            model="MLD_LX2", project_id=project.id,
                            project_pinned=True)
        unassigned_dev = Device(serial="S-none-1", host_id="h-attr", status="ONLINE",
                                model="MLD_LX3")
        db_session.add_all([rule_dev, manual_dev, pinned_dev, unassigned_dev])
        db_session.commit()
        return rule_dev, manual_dev, unassigned_dev

    def test_attribution_source_tristate(self, client, db_session, auth_headers):
        """型号命中 match_models → rule；钉住 → pinned；归属但不在规则 → manual；无项目 → unassigned。"""
        rule_dev, manual_dev, unassigned_dev = self._seed(db_session)

        response = client.get("/api/v1/devices", headers=auth_headers)
        assert response.status_code == 200
        by_serial = {d["serial"]: d for d in response.json()}
        assert by_serial[rule_dev.serial]["attribution_source"] == "rule"
        assert by_serial["S-pinned-1"]["attribution_source"] == "pinned"
        assert by_serial[manual_dev.serial]["attribution_source"] == "manual"
        assert by_serial[unassigned_dev.serial]["attribution_source"] == "unassigned"
        assert by_serial[unassigned_dev.serial]["project_key"] is None

    def test_unassigned_filter_only_returns_unattached(self, client, db_session, auth_headers):
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
