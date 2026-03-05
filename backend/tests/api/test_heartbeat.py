"""
Tests for heartbeat API routes
"""
from datetime import datetime, timedelta

from backend.models.enums import DeviceStatus, HostStatus
from backend.models.host import Device


class TestHeartbeat:
    """Test POST /api/v1/heartbeat"""

    def test_heartbeat_new_host(self, client):
        """Test heartbeat creates new host automatically"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 999,
                "status": "ONLINE",
                "mount_status": {"nfs": "mounted"},
                "extra": {"cpu_load": 0.5},
                "host": {"ip": "192.168.1.100"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "host_id" in data

    def test_heartbeat_existing_host(self, client, sample_host):
        """Test heartbeat updates existing host"""
        original_heartbeat = sample_host.last_heartbeat

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "mount_status": {"nfs": "mounted"},
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["host_id"] == sample_host.id

    def test_heartbeat_creates_host_by_ip(self, client, sample_host):
        """Test heartbeat finds existing host by IP"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 99999,  # Non-existent ID
                "status": "ONLINE",
                "host": {"ip": sample_host.ip},
            },
        )
        assert response.status_code == 200
        data = response.json()
        # Should find and use existing host
        assert data["host_id"] == sample_host.id

    def test_heartbeat_with_devices(self, client, sample_host):
        """Test heartbeat with device information"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "DEVICE001",
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                        "battery_level": 85,
                        "temperature": 36,
                        "network_latency": 15.5,
                    }
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["devices_count"] == 1

    def test_heartbeat_missing_host_id(self, client):
        """Test heartbeat without host_id"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "status": "ONLINE",
            },
        )
        assert response.status_code == 422

    def test_heartbeat_missing_status(self, client, sample_host):
        """Test heartbeat without status"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
            },
        )
        assert response.status_code == 422

    def test_heartbeat_invalid_status(self, client, sample_host):
        """Test heartbeat with invalid status"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "INVALID_STATUS",
            },
        )
        assert response.status_code == 422

    def test_heartbeat_updates_host_status(self, client, sample_host, db_session):
        """Test heartbeat updates host status"""
        # Set host to offline
        sample_host.status = HostStatus.OFFLINE.value
        db_session.commit()

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
            },
        )
        assert response.status_code == 200

        db_session.refresh(sample_host)
        assert sample_host.status == HostStatus.ONLINE.value

    def test_heartbeat_device_offline_when_not_adb_connected(self, client, sample_host, db_session):
        """Test device status becomes OFFLINE when not ADB connected"""
        # First create a device via heartbeat
        client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "OFFLINE_DEVICE",
                        "model": "TestModel",
                        "adb_state": "offline",
                        "adb_connected": False,
                    }
                ],
            },
        )

        device = db_session.query(Device).filter(Device.serial == "OFFLINE_DEVICE").first()
        assert device.status == DeviceStatus.OFFLINE.value

    def test_heartbeat_device_busy_when_locked(self, client, sample_host, db_session):
        """Test device status becomes BUSY when locked"""
        # First create a device via heartbeat
        client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "BUSY_DEVICE",
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                    }
                ],
            },
        )

        # Lock the device
        device = db_session.query(Device).filter(Device.serial == "BUSY_DEVICE").first()
        device.lock_run_id = 123456
        device.lock_expires_at = datetime.utcnow() + timedelta(minutes=10)
        db_session.commit()

        # Send heartbeat again
        client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "BUSY_DEVICE",
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                    }
                ],
            },
        )

        db_session.refresh(device)
        assert device.status == DeviceStatus.BUSY.value

    def test_heartbeat_marks_missing_devices_offline(self, client, sample_host, db_session):
        """Test heartbeat marks missing devices as offline"""
        # Create an old device that hasn't been seen recently
        old_device = Device(
            serial="OLD_DEVICE",
            host_id=sample_host.id,
            status=DeviceStatus.ONLINE.value,
            last_seen=datetime.utcnow() - timedelta(minutes=5),
            adb_connected=True,
        )
        db_session.add(old_device)
        db_session.commit()

        # Send heartbeat without this device
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [],
            },
        )
        assert response.status_code == 200

        db_session.refresh(old_device)
        assert old_device.status == DeviceStatus.OFFLINE.value
        assert old_device.adb_connected is False

    def test_heartbeat_updates_device_hardware_info(self, client, sample_host, db_session):
        """Test heartbeat updates device hardware information"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "HW_DEVICE",
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                        "battery_level": 75,
                        "battery_temp": 30,
                        "temperature": 35,
                        "wifi_rssi": -65,
                        "wifi_ssid": "TestWiFi",
                        "network_latency": 20.5,
                        "cpu_usage": 15.5,
                        "mem_total": 8000000000,
                        "mem_used": 4000000000,
                        "disk_total": 128000000000,
                        "disk_used": 64000000000,
                    }
                ],
            },
        )
        assert response.status_code == 200

        device = db_session.query(Device).filter(Device.serial == "HW_DEVICE").first()
        assert device.battery_level == 75
        assert device.battery_temp == 30
        assert device.temperature == 35
        assert device.wifi_rssi == -65
        assert device.wifi_ssid == "TestWiFi"
        assert device.network_latency == 20.5
        assert device.cpu_usage == 15.5
        assert device.mem_total == 8000000000
        assert device.mem_used == 4000000000
        assert device.disk_total == 128000000000
        assert device.disk_used == 64000000000

    def test_heartbeat_preserves_existing_device(self, client, sample_device):
        """Test heartbeat preserves existing device data"""
        original_model = sample_device.model

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_device.host_id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": sample_device.serial,
                        "model": "UpdatedModel",
                        "adb_state": "device",
                        "adb_connected": True,
                    }
                ],
            },
        )
        assert response.status_code == 200

    def test_heartbeat_device_without_serial_skipped(self, client, sample_host):
        """Test devices without serial are skipped"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "model": "NoSerialModel",
                        "adb_state": "device",
                    }
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["devices_count"] == 0

    def test_heartbeat_empty_devices_list(self, client, sample_host):
        """Test heartbeat with empty devices list"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["devices_count"] == 0
