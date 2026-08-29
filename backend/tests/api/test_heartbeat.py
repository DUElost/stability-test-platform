"""
Tests for heartbeat API routes
"""
from datetime import datetime, timedelta, timezone

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

    def test_heartbeat_ip_conflict_keeps_old_ip(self, client, sample_host, db_session):
        """#101: host.ip 唯一后，上报被其他 host 占用的 IP 不覆盖、不 500。"""
        from backend.models.host import Host

        host_b = Host(
            id="104",
            hostname="test-host-104",
            name="test-host-b",
            ip="192.0.2.104",
            ip_address="192.0.2.104",
            status=HostStatus.ONLINE.value,
        )
        db_session.add(host_b)
        db_session.commit()

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": host_b.id,
                "status": "ONLINE",
                "host": {"ip": sample_host.ip},
            },
        )
        assert response.status_code == 200, response.text
        db_session.refresh(host_b)
        assert host_b.ip == "192.0.2.104"  # 保留旧 IP，不被占用方覆盖

    def test_heartbeat_updates_script_catalog_version_and_reports_outdated(
        self, client, sample_host, db_session
    ):
        sample_host.script_catalog_version = "old-script-version"
        db_session.commit()

        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "script_catalog_version": "new-script-version",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["script_catalog_outdated"] is True
        db_session.refresh(sample_host)
        assert sample_host.script_catalog_version == "new-script-version"

    def test_heartbeat_not_outdated_when_agent_matches_the_server_catalog(
        self, client, sample_host, db_session
    ):
        """对上服务端当前目录就不该再让 Agent 重拉。"""
        from backend.services.script_catalog_version import compute_script_catalog_version

        current = compute_script_catalog_version(db_session)
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "script_catalog_version": current,
            },
        )
        assert response.status_code == 200
        assert response.json()["script_catalog_outdated"] is False

    def test_stale_agent_stays_outdated_across_repeated_heartbeats(
        self, client, sample_host, db_session
    ):
        """旧逻辑的真正缺陷：它比的是「这个 Agent 上次报的值」。

        于是同一个陈旧值连报两次，第二次就被判为「已同步」——控制面新发布的
        脚本版本永远送不到运行中的 Agent，直到很久以后作业执行时才以
        ``ScriptVersionMismatch`` 爆出来，而唯一的解法是手动重启每台 Agent。
        """
        stale = "stale-agent-digest"
        for _ in range(2):
            response = client.post(
                "/api/v1/heartbeat",
                json={
                    "host_id": sample_host.id,
                    "status": "ONLINE",
                    "script_catalog_version": stale,
                },
            )
            assert response.status_code == 200
            assert response.json()["script_catalog_outdated"] is True

    def test_publishing_a_script_version_makes_an_in_sync_agent_outdated(
        self, client, sample_host, db_session
    ):
        from backend.models.script import Script
        from backend.services.script_catalog_version import compute_script_catalog_version

        in_sync = compute_script_catalog_version(db_session)
        payload = {
            "host_id": sample_host.id,
            "status": "ONLINE",
            "script_catalog_version": in_sync,
        }
        assert client.post("/api/v1/heartbeat", json=payload).json()[
            "script_catalog_outdated"
        ] is False

        db_session.add(Script(
            name="freshly_published", display_name="freshly_published",
            category="device", script_type="python", version="2.0.0",
            nfs_path="/s/freshly_published/v2.0.0/freshly_published.py",
            content_sha256="sha-new", param_schema={}, default_params={},
            is_active=True,
        ))
        db_session.commit()

        # Agent 还没重拉，报的仍是旧摘要 —— 必须被判为过期。
        assert client.post("/api/v1/heartbeat", json=payload).json()[
            "script_catalog_outdated"
        ] is True

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

    def test_heartbeat_auto_register_sentinel_creates_distinct_hosts_per_ip(self, client):
        """Test host_id=0 auto-register does not collapse multiple hosts into one row"""
        first = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 0,
                "status": "ONLINE",
                "host": {"ip": "198.18.0.1"},
            },
        )
        second = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 0,
                "status": "ONLINE",
                "host": {"ip": "203.0.113.36"},
            },
        )

        assert first.status_code == 200
        assert second.status_code == 200

        first_host_id = first.json()["host_id"]
        second_host_id = second.json()["host_id"]

        assert first_host_id != "0"
        assert second_host_id != "0"
        assert first_host_id != second_host_id
        assert first_host_id == "198-18-0-1"
        assert second_host_id == "203-0-113-36"

    def test_heartbeat_updates_existing_host_ip_from_payload(self, client, sample_host, db_session):
        """Test heartbeat refreshes displayed host IP when agent reports a new address"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "host": {"ip": "203.0.113.36"},
            },
        )

        assert response.status_code == 200

        db_session.refresh(sample_host)
        assert sample_host.ip == "203.0.113.36"
        assert sample_host.ip_address == "203.0.113.36"

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

    def test_heartbeat_device_error_when_adb_unauthorized(self, client, sample_host, db_session):
        """issue #52: adb 已发现设备但 adb_state 非 'device'（如 unauthorized）应判定为
        ERROR，区别于纯粹未被发现的 OFFLINE。"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "UNAUTHORIZED_DEVICE",
                        "model": "TestModel",
                        "adb_state": "unauthorized",
                        "adb_connected": False,
                    }
                ],
            },
        )
        assert response.status_code == 200

        device = db_session.query(Device).filter(Device.serial == "UNAUTHORIZED_DEVICE").first()
        assert device.status == DeviceStatus.ERROR.value

    def test_heartbeat_device_busy_when_locked(self, client, sample_host, db_session):
        """Phase 6c: device status becomes BUSY when it has an active DeviceLease."""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus, LeaseType

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

        device = db_session.query(Device).filter(Device.serial == "BUSY_DEVICE").first()

        # Phase 6c: create an ACTIVE DeviceLease instead of setting lock_run_id
        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=device.id,
            job_id=None,
            host_id=sample_host.id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{device.id}:1",
            lease_generation=1,
            agent_instance_id=sample_host.id,
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db_session.add(lease)
        db_session.commit()

        # Send heartbeat again — should detect active lease and set BUSY
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
            last_seen=datetime.now(timezone.utc) - timedelta(minutes=5),
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

    def test_heartbeat_notifies_missing_device_offline_only_on_transition(
        self, client, sample_host, db_session, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            "backend.services.notification_service.dispatch_notification_async",
            lambda event_type, context: calls.append((event_type, context["device_serial"])),
        )
        old_device = Device(
            serial="OLD_NOTIFY_DEVICE",
            host_id=sample_host.id,
            status=DeviceStatus.ONLINE.value,
            last_seen=datetime.now(timezone.utc) - timedelta(minutes=5),
            adb_connected=True,
        )
        db_session.add(old_device)
        db_session.commit()

        payload = {
            "host_id": sample_host.id,
            "status": "ONLINE",
            "devices": [],
        }

        assert client.post("/api/v1/heartbeat", json=payload).status_code == 200
        assert calls == [("DEVICE_OFFLINE", "OLD_NOTIFY_DEVICE")]

        assert client.post("/api/v1/heartbeat", json=payload).status_code == 200
        assert calls == [("DEVICE_OFFLINE", "OLD_NOTIFY_DEVICE")]

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


class TestHeartbeatDevicePlatform:
    """#73: SoC 平台随心跳入库,用于 AEE(MTK 专有)门禁与按平台筛选。"""

    def _post(self, client, host_id, serial, platform):
        return client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": host_id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": serial,
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                        "platform": platform,
                    }
                ],
            },
        )

    def test_platform_persisted_on_device_create(self, client, sample_host, db_session):
        self._post(client, sample_host.id, "PLATFORM_NEW", "UNISOC")

        device = db_session.query(Device).filter(Device.serial == "PLATFORM_NEW").first()
        assert device.platform == "UNISOC"

    def test_platform_updated_on_existing_device(self, client, sample_host, db_session):
        self._post(client, sample_host.id, "PLATFORM_UPD", "UNKNOWN")
        self._post(client, sample_host.id, "PLATFORM_UPD", "MTK")

        db_session.expire_all()
        device = db_session.query(Device).filter(Device.serial == "PLATFORM_UPD").first()
        assert device.platform == "MTK", "首次判不出、后续判出时应写入确定值"

    def test_unknown_does_not_overwrite_known_platform(self, client, sample_host, db_session):
        """adb 抖动一次上报 UNKNOWN,不能把已判定的 MTK 抹掉。"""
        self._post(client, sample_host.id, "PLATFORM_KEEP", "MTK")
        self._post(client, sample_host.id, "PLATFORM_KEEP", "UNKNOWN")

        db_session.expire_all()
        device = db_session.query(Device).filter(Device.serial == "PLATFORM_KEEP").first()
        assert device.platform == "MTK"

    def test_missing_platform_field_is_tolerated(self, client, sample_host, db_session):
        """老版本 Agent 不上报 platform — 不能因此 500 或写脏值。"""
        response = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "PLATFORM_ABSENT",
                        "model": "TestModel",
                        "adb_state": "device",
                        "adb_connected": True,
                    }
                ],
            },
        )

        assert response.status_code == 200
        device = db_session.query(Device).filter(Device.serial == "PLATFORM_ABSENT").first()
        assert device.platform is None


class TestHeartbeatProjectAttribution:
    """ADR-0029 P1：心跳路径应用归属规则（新建 / model 变更 / 未归属）。

    触发条件收窄为三个——稳态下零额外查询；pinned 设备永不被规则覆盖。
    """

    @staticmethod
    def _seed_rule(db_session, project, model):
        from backend.models.project_rule import ProjectDeviceRule

        rule = ProjectDeviceRule(project_id=project.id, match_value=model)
        db_session.add(rule)
        db_session.commit()
        return rule

    def _heartbeat(self, client, host_id, serial, model, *, adb_connected=True):
        return client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": host_id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": serial,
                        "model": model,
                        "adb_state": "device",
                        "adb_connected": adb_connected,
                    }
                ],
            },
        )

    def test_new_device_auto_attributed(self, client, db_session, sample_host):
        from backend.models.host import Device
        from backend.models.project import TestProject

        project = TestProject(project_key="HB-A", display_name="hb", source="USER")
        db_session.add(project)
        db_session.commit()
        self._seed_rule(db_session, project, "MLD_LX2")

        resp = self._heartbeat(client, sample_host.id, "HB-NEW-1", "MLD_LX2")
        assert resp.status_code == 200
        device = db_session.query(Device).filter(Device.serial == "HB-NEW-1").one()
        assert device.project_id == project.id

    def test_model_change_reapplies(self, client, db_session, sample_host):
        from backend.models.host import Device
        from backend.models.project import TestProject

        project_a = TestProject(project_key="HB-A", display_name="a", source="USER")
        project_b = TestProject(project_key="HB-B", display_name="b", source="USER")
        db_session.add_all([project_a, project_b])
        db_session.commit()
        self._seed_rule(db_session, project_a, "MLD_LX2")
        self._seed_rule(db_session, project_b, "MLD_LX3")

        self._heartbeat(client, sample_host.id, "HB-CHG-1", "MLD_LX2")
        device = db_session.query(Device).filter(Device.serial == "HB-CHG-1").one()
        assert device.project_id == project_a.id

        # model 变更 → 按新型号规则重解析
        self._heartbeat(client, sample_host.id, "HB-CHG-1", "MLD_LX3")
        db_session.refresh(device)
        assert device.project_id == project_b.id

    def test_unattributed_gets_attributed_on_next_beat(
        self, client, db_session, sample_host
    ):
        from backend.models.host import Device
        from backend.models.project import TestProject

        project = TestProject(project_key="HB-A", display_name="a", source="USER")
        db_session.add(project)
        db_session.commit()
        self._heartbeat(client, sample_host.id, "HB-LATE-1", "MLD_LX2")
        device = db_session.query(Device).filter(Device.serial == "HB-LATE-1").one()
        assert device.project_id is None

        self._seed_rule(db_session, project, "MLD_LX2")
        self._heartbeat(client, sample_host.id, "HB-LATE-1", "MLD_LX2")
        db_session.refresh(device)
        assert device.project_id == project.id

    def test_pinned_device_not_overwritten(self, client, db_session, sample_host):
        from backend.models.host import Device
        from backend.models.project import TestProject

        project_a = TestProject(project_key="HB-A", display_name="a", source="USER")
        project_b = TestProject(project_key="HB-B", display_name="b", source="USER")
        db_session.add_all([project_a, project_b])
        db_session.commit()
        self._seed_rule(db_session, project_b, "MLD_LX3")
        self._heartbeat(client, sample_host.id, "HB-PIN-1", "MLD_LX2")
        device = db_session.query(Device).filter(Device.serial == "HB-PIN-1").one()
        device.project_id = project_a.id
        device.project_pinned = True
        db_session.commit()

        # pinned 设备即使型号命中其他项目规则也不被覆盖
        self._heartbeat(client, sample_host.id, "HB-PIN-1", "MLD_LX3")
        db_session.refresh(device)
        assert device.project_id == project_a.id
        assert device.project_pinned is True
