"""
Tests for devices API routes
"""
import pytest
from datetime import datetime, timedelta


class TestCreateDevice:
    """Test POST /api/v1/devices"""

    def test_create_device_success(self, client, sample_host, auth_headers):
        """Test creating a new device successfully"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": "NEW001",
                "model": "NewModel",
                "host_id": sample_host.id,
                "tags": ["test", "new"],
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["serial"] == "NEW001"
        assert data["model"] == "NewModel"
        assert data["host_id"] == sample_host.id
        assert data["tags"] == ["test", "new"]
        assert data["status"] == "OFFLINE"
        assert "id" in data

    def test_create_device_duplicate_serial(self, client, sample_device, auth_headers):
        """Test creating device with duplicate serial fails"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": sample_device.serial,
                "model": "DuplicateModel",
                "host_id": sample_device.host_id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert "already exists" in response.json()["detail"]

    def test_create_device_missing_serial(self, client, sample_host, auth_headers):
        """Test creating device without serial fails"""
        response = client.post(
            "/api/v1/devices",
            json={
                "model": "NoSerialModel",
                "host_id": sample_host.id,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_create_device_invalid_host(self, client, auth_headers):
        """Test creating device with non-existent host"""
        response = client.post(
            "/api/v1/devices",
            json={
                "serial": "INVALID001",
                "model": "InvalidModel",
                "host_id": 99999,
            },
            headers=auth_headers,
        )
        # Should succeed as host_id is not validated in create_device
        assert response.status_code == 200


class TestListDevices:
    """Test GET /api/v1/devices"""

    def test_list_devices_empty(self, client):
        """Test listing devices when empty"""
        response = client.get("/api/v1/devices")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_list_devices_with_data(self, client, sample_device):
        """Test listing devices with data"""
        response = client.get("/api/v1/devices")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["serial"] == sample_device.serial
        assert data[0]["model"] == sample_device.model

    def test_list_devices_ordered_by_id(self, client, sample_host, auth_headers):
        """Test devices are ordered by id"""
        # Create multiple devices
        for i in range(3):
            client.post(
                "/api/v1/devices",
                json={
                    "serial": f"ORDER{i}",
                    "model": "OrderModel",
                    "host_id": sample_host.id,
                },
                headers=auth_headers,
            )

        response = client.get("/api/v1/devices")
        data = response.json()
        assert len(data) == 3
        ids = [d["id"] for d in data]
        assert ids == sorted(ids)

    def test_list_devices_status_offline_when_host_offline(
        self, client, db_session, sample_device, sample_offline_host
    ):
        """Test device status becomes OFFLINE when host is offline"""
        # Move device to offline host
        sample_device.host_id = sample_offline_host.id
        db_session.commit()

        response = client.get("/api/v1/devices")
        assert response.status_code == 200
        data = response.json()
        assert data[0]["status"] == "OFFLINE"


class TestGetDevice:
    """Test GET /api/v1/devices/{device_id}"""

    def test_get_device_success(self, client, sample_device):
        """Test getting a device by id"""
        response = client.get(f"/api/v1/devices/{sample_device.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == sample_device.id
        assert data["serial"] == sample_device.serial
        assert data["model"] == sample_device.model

    def test_get_device_not_found(self, client):
        """Test getting non-existent device"""
        response = client.get("/api/v1/devices/99999")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_get_device_invalid_id(self, client):
        """Test getting device with invalid id"""
        response = client.get("/api/v1/devices/invalid")
        assert response.status_code == 422

    def test_get_device_status_updated_when_host_offline(
        self, client, db_session, sample_device, sample_offline_host
    ):
        """Test device status is updated when host is offline"""
        # Move device to offline host
        sample_device.host_id = sample_offline_host.id
        db_session.commit()

        response = client.get(f"/api/v1/devices/{sample_device.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"

    def test_get_device_status_offline_when_heartbeat_expired(
        self, client, db_session, sample_device, sample_host_expired
    ):
        """Test device status becomes OFFLINE when host heartbeat expired"""
        # Move device to host with expired heartbeat
        sample_device.host_id = sample_host_expired.id
        db_session.commit()

        response = client.get(f"/api/v1/devices/{sample_device.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "OFFLINE"


class TestDeviceWithHostRelationship:
    """Test device-host relationship scenarios"""

    def test_device_includes_host_info(self, client, sample_device):
        """Test device response includes host relationship"""
        response = client.get(f"/api/v1/devices/{sample_device.id}")
        assert response.status_code == 200
        data = response.json()
        assert "host_id" in data
        assert data["host_id"] == sample_device.host_id

    def test_device_without_host(self, client, db_session):
        """Test device without host association"""
        import sys
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from backend.models.schemas import Device, DeviceStatus

        device = Device(
            serial="NOHOST001",
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow(),
        )
        db_session.add(device)
        db_session.commit()

        response = client.get(f"/api/v1/devices/{device.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["host_id"] is None
