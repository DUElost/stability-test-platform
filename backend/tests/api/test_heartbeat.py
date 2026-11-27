"""
Tests for heartbeat API routes
"""
import logging
from datetime import datetime, timedelta, timezone

HEARTBEAT_LOGGER = "backend.api.routes.heartbeat"

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

    def test_heartbeat_clears_stale_network_latency_when_probed(
        self, client, db_session, sample_host
    ):
        """#4166：明确探测过网络且 ping 失败（network_latency=None + probed=True）须清空旧延时；
        未携带 probed 标记的旧版 Agent 或未采样拍仍保留旧值。"""
        # 1) 首次上报有网延时
        resp1 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "NET_CLEAR_DEV",
                        "adb_state": "device",
                        "adb_connected": True,
                        "battery_level": 90,
                        "network_latency": 28.4,
                        "network_latency_probed": True,
                    }
                ],
            },
        )
        assert resp1.status_code == 200
        device = db_session.query(Device).filter(Device.serial == "NET_CLEAR_DEV").first()
        assert device is not None
        assert device.network_latency == 28.4

        # 复位硬件快照时间窗，使下一次心跳进入 write_hw
        device.hardware_updated_at = None
        db_session.commit()

        # 2) 旧版 Agent / 未探测拍上报 network_latency=None（无 probed 标记）→ 保留旧值
        resp2 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "NET_CLEAR_DEV",
                        "adb_state": "device",
                        "adb_connected": True,
                        "battery_level": 89,
                        "network_latency": None,
                    }
                ],
            },
        )
        assert resp2.status_code == 200
        db_session.refresh(device)
        assert device.network_latency == 28.4

        device.hardware_updated_at = None
        db_session.commit()

        # 3) 新版 Agent 实测断网（network_latency=None + network_latency_probed=True）→ 清空为 None
        resp3 = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": sample_host.id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": "NET_CLEAR_DEV",
                        "adb_state": "device",
                        "adb_connected": True,
                        "battery_level": 88,
                        "network_latency": None,
                        "network_latency_probed": True,
                    }
                ],
            },
        )
        assert resp3.status_code == 200
        db_session.refresh(device)
        assert device.network_latency is None

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


class TestHeartbeatHostReassignment:
    """#2569：设备被另一台 host 抢走时，日志里必须看得见**成因**。

    保护链原本只在两处：有 ACTIVE 租约时 `device_host_reassignment_blocked`（阻断），
    以及派发侧 `device_host_drift` 整批 fatal（后果）。中间那段「无租约窗口里的改绑」
    只有**占位 serial** 才留日志——非占位 serial 跨 host 漂移是彻底静默的，事后
    只能看见 fatal 的果，查不到是谁在争。本单**只补日志、不改语义**：归属仍按
    最新心跳走（这与 #1356 对占位值的态度一致）。
    """

    @staticmethod
    def _beat(client, ip, serial):
        return client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": 0,  # 自动注册哨兵：按 IP 建/找 host（与夹具同一条路径）
                "status": "ONLINE",
                "host": {"ip": ip},
                "devices": [{"serial": serial, "adb_state": "device",
                             "adb_connected": True}],
            },
        )

    def test_non_placeholder_reassignment_is_logged(
        self, client, db_session, caplog,
    ):
        serial = "RealSerial-2569-A"
        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        assert self._beat(client, "198.18.0.61", serial).status_code == 200
        first = db_session.query(Device).filter(Device.serial == serial).one()
        owner = first.host_id
        # 同一台 host 再报一次：不该出现改绑日志（否则这条日志就成了每拍噪声）
        caplog.clear()
        self._beat(client, "198.18.0.61", serial)
        assert not [r for r in caplog.records if "device_host_reassigned" in r.getMessage()]

        caplog.clear()
        assert self._beat(client, "198.18.0.62", serial).status_code == 200
        db_session.expire_all()
        moved = db_session.query(Device).filter(Device.serial == serial).one()
        assert moved.host_id != owner, (
            "语义前提不成立：无租约时归属本应按最新心跳改绑"
        )
        hits = [r.getMessage() for r in caplog.records
                if "device_host_reassigned" in r.getMessage()]
        assert len(hits) == 1, (
            f"非占位 serial 被跨 host 改绑必须留一条成因日志：{hits}"
        )
        msg = hits[0]
        assert serial in msg and f"{owner}->" in msg.replace(f"{owner} ->", f"{owner}->"), (
            f"日志要能回答「哪台设备、从哪漂到哪」：{msg}"
        )
        assert "placeholder_serial_host_drift" not in msg, (
            "非占位值不该被报成占位值——那会把运维引向刷机而不是查两台 agent"
        )

    def test_placeholder_reassignment_keeps_its_own_event(
        self, client, db_session, caplog,
    ):
        """占位值走的是**另一个**事件名（#1356 的处置），不能被本单合并掉。

        两条路径的运维动作不同：占位值建议刷机/换线让设备上报真实 serial；非占位值
        说明两台 agent 在抢同一台真机。合并成一个事件就只剩一个模糊的告警。
        """
        serial = "0123456789ABCDEF"  # 已知占位值
        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        self._beat(client, "198.18.0.63", serial)
        caplog.clear()
        self._beat(client, "198.18.0.64", serial)
        msgs = [r.getMessage() for r in caplog.records
                if "placeholder_serial_host_drift" in r.getMessage()]
        assert len(msgs) == 1, msgs
        assert not [r for r in caplog.records
                    if "device_host_reassigned" in r.getMessage()], (
            "占位值不该被当成「两台 agent 抢真机」"
        )

    def test_leased_device_is_blocked_before_it_is_reassigned(
        self, client, db_session, caplog, sample_host, sample_device,
    ):
        """租约保护仍是第一道：被 block 的行不得再打改绑日志（它没被改绑）。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus, LeaseType

        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=sample_device.id,
            job_id=None,
            host_id=sample_device.host_id,
            lease_type=LeaseType.JOB.value,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=f"{sample_device.id}:1",
            lease_generation=1,
            agent_instance_id=sample_device.host_id,
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(minutes=10),
        )
        db_session.add(lease)
        db_session.commit()
        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        self._beat(client, "198.18.0.65", sample_device.serial)
        msgs = [r.getMessage() for r in caplog.records]
        assert any("device_host_reassignment_blocked" in m for m in msgs), msgs
        assert not any("device_host_reassigned" in m for m in msgs), (
            "阻断路径已经 return/continue，不该再落一条「改绑成功」的日志"
        )
        db_session.expire_all()
        kept = db_session.query(Device).filter(
            Device.serial == sample_device.serial
        ).one()
        assert kept.host_id == sample_device.host_id


class TestHeartbeatMaintenanceLeaseHostReassignment3856:
    """#3856：MAINTENANCE 欠账租约随物理挪位迁移 host，JOB 仍阻断，设备不再失踪。"""

    @staticmethod
    def _make_host(db_session, *, host_id: str, ip: str):
        from backend.models.host import Host
        from backend.models.enums import HostStatus

        host = Host(
            id=host_id,
            hostname=f"host-{host_id}",
            name=f"host-{host_id}",
            ip=ip,
            ip_address=ip,
            status=HostStatus.ONLINE.value,
            last_heartbeat=datetime.now(timezone.utc),
        )
        db_session.add(host)
        db_session.flush()
        return host

    @staticmethod
    def _make_device(db_session, *, serial: str, host_id: str, status: str = "BUSY"):
        device = Device(
            serial=serial,
            host_id=host_id,
            status=status,
            last_seen=datetime.now(timezone.utc) - timedelta(minutes=5),
            adb_connected=True,
            adb_state="device",
            lease_generation=1,
        )
        db_session.add(device)
        db_session.flush()
        return device

    @staticmethod
    def _make_lease(
        db_session,
        *,
        device,
        host_id: str,
        lease_type: str,
        reason: str | None = None,
        holder: str | None = None,
        fencing_token: str | None = None,
    ):
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus

        now = datetime.now(timezone.utc)
        lease = DeviceLease(
            device_id=device.id,
            job_id=None,
            host_id=host_id,
            lease_type=lease_type,
            status=LeaseStatus.ACTIVE.value,
            fencing_token=fencing_token or f"{device.id}:1",
            lease_generation=1,
            agent_instance_id=host_id,
            reason=reason,
            holder=holder,
            acquired_at=now,
            renewed_at=now,
            expires_at=now + timedelta(hours=1),
        )
        db_session.add(lease)
        db_session.flush()
        return lease

    @staticmethod
    def _make_open_debt(db_session, *, device_id: int, fencing_token: str):
        from backend.models.device_return_debt import DeviceReturnDebt

        debt = DeviceReturnDebt(
            device_id=device_id,
            family="power-cycle",
            source_kind="stock",
            source_job_id=None,
            source_fencing_token=fencing_token,
            cause="not_executed",
            state="open",
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(debt)
        db_session.flush()
        return debt

    @staticmethod
    def _beat(client, host_id: str, serial: str):
        return client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": host_id,
                "status": "ONLINE",
                "devices": [
                    {
                        "serial": serial,
                        "adb_state": "device",
                        "adb_connected": True,
                    }
                ],
            },
        )

    def test_active_job_lease_still_blocks_host_rebind(
        self, client, db_session, caplog,
    ):
        """① ACTIVE JOB 跨 host 心跳仍阻断 device/lease host 改绑。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseType

        host_a = self._make_host(db_session, host_id="3856-a-job", ip="198.18.56.1")
        host_b = self._make_host(db_session, host_id="3856-b-job", ip="198.18.56.2")
        device = self._make_device(
            db_session, serial="3856-JOB-REAL", host_id=host_a.id,
        )
        lease = self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.JOB.value,
        )
        db_session.commit()
        before_token = lease.fencing_token
        before_seen = device.last_seen

        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        assert self._beat(client, host_b.id, device.serial).status_code == 200

        db_session.expire_all()
        device = db_session.query(Device).filter(Device.serial == device.serial).one()
        lease = db_session.get(DeviceLease, lease.id)
        assert device.host_id == host_a.id
        assert lease.host_id == host_a.id
        assert lease.status == "ACTIVE"
        assert lease.fencing_token == before_token
        assert device.last_seen == before_seen
        assert any(
            "device_host_reassignment_blocked" in r.getMessage()
            for r in caplog.records
        )

    def test_debt_maintenance_lease_migrates_with_device(
        self, client, db_session, caplog,
    ):
        """② 欠账 MAINTENANCE：device+lease 同步迁至新 host，status=BUSY，欠账可查。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseType
        from backend.services.device_return import (
            DEBT_LEASE_HOLDER,
            DEBT_LEASE_REASON,
            is_debt_lease,
            open_debts_for_host_sync,
        )

        host_a = self._make_host(db_session, host_id="3856-a-debt", ip="198.18.56.3")
        host_b = self._make_host(db_session, host_id="3856-b-debt", ip="198.18.56.4")
        device = self._make_device(
            db_session, serial="3856-DEBT-REAL", host_id=host_a.id,
        )
        token = f"{device.id}:7"
        lease = self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.MAINTENANCE.value,
            reason=DEBT_LEASE_REASON,
            holder=DEBT_LEASE_HOLDER,
            fencing_token=token,
        )
        self._make_open_debt(
            db_session, device_id=device.id, fencing_token=token,
        )
        db_session.commit()
        assert is_debt_lease(lease)

        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        assert self._beat(client, host_b.id, device.serial).status_code == 200

        db_session.expire_all()
        device = db_session.query(Device).filter(Device.serial == device.serial).one()
        lease = db_session.get(DeviceLease, lease.id)
        assert device.host_id == host_b.id
        assert lease.host_id == host_b.id
        assert lease.status == "ACTIVE"
        assert lease.fencing_token == token
        assert is_debt_lease(lease)
        assert device.status == "BUSY"
        assert device.adb_state == "device"
        assert device.adb_connected is True
        assert device.last_seen is not None
        debts = open_debts_for_host_sync(db_session, host_b.id)
        assert any(d["device_id"] == device.id for d in debts), debts
        assert any(
            "maintenance_debt_lease_host_migrated" in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

    def test_migrated_debt_still_blocks_dispatch(self, client, db_session):
        """③ 挪机后派发分类仍因 device_return_debt / active_lease 拒绝。"""
        from backend.models.enums import LeaseType
        from backend.services.device_return import DEBT_LEASE_HOLDER, DEBT_LEASE_REASON
        from backend.services.plan_dispatcher_sync import _classify_dispatch_devices_sync

        host_a = self._make_host(db_session, host_id="3856-a-adm", ip="198.18.56.5")
        host_b = self._make_host(db_session, host_id="3856-b-adm", ip="198.18.56.6")
        device = self._make_device(
            db_session, serial="3856-DEBT-ADM", host_id=host_a.id,
        )
        token = f"{device.id}:3"
        self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.MAINTENANCE.value,
            reason=DEBT_LEASE_REASON,
            holder=DEBT_LEASE_HOLDER,
            fencing_token=token,
        )
        self._make_open_debt(
            db_session, device_id=device.id, fencing_token=token,
        )
        db_session.commit()

        assert self._beat(client, host_b.id, device.serial).status_code == 200
        db_session.expire_all()
        device = db_session.query(Device).filter(Device.serial == device.serial).one()
        assert device.host_id == host_b.id

        unavailable, _ = _classify_dispatch_devices_sync(db_session, [device.id])
        reasons = {u["reason"] for u in unavailable if u["id"] == device.id}
        assert reasons & {"device_return_debt", "active_lease"}, unavailable

    def test_placeholder_debt_maintenance_does_not_migrate_lease(
        self, client, db_session, caplog,
    ):
        """④ 占位 serial 持欠账 MAINTENANCE：不迁移 lease.host_id。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseType
        from backend.services.device_return import DEBT_LEASE_HOLDER, DEBT_LEASE_REASON

        host_a = self._make_host(db_session, host_id="3856-a-ph", ip="198.18.56.7")
        host_b = self._make_host(db_session, host_id="3856-b-ph", ip="198.18.56.8")
        device = self._make_device(
            db_session, serial="0123456789ABCDEF", host_id=host_a.id,
        )
        token = f"{device.id}:9"
        lease = self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.MAINTENANCE.value,
            reason=DEBT_LEASE_REASON,
            holder=DEBT_LEASE_HOLDER,
            fencing_token=token,
        )
        self._make_open_debt(
            db_session, device_id=device.id, fencing_token=token,
        )
        db_session.commit()
        owner_host = host_a.id

        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        assert self._beat(client, host_b.id, device.serial).status_code == 200

        db_session.expire_all()
        device = db_session.query(Device).filter(
            Device.serial == "0123456789ABCDEF"
        ).one()
        lease = db_session.get(DeviceLease, lease.id)
        assert lease.host_id == owner_host
        assert lease.status == "ACTIVE"
        assert lease.fencing_token == token
        assert device.host_id == owner_host
        assert any(
            "device_host_reassignment_blocked" in r.getMessage()
            for r in caplog.records
        )

    def test_non_debt_maintenance_updates_device_keeps_lease_host(
        self, client, db_session, caplog,
    ):
        """⑤ 非欠账 MAINTENANCE：device 迁至 B 且 BUSY，lease 仍钉在 A。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseType

        host_a = self._make_host(db_session, host_id="3856-a-man", ip="198.18.56.9")
        host_b = self._make_host(db_session, host_id="3856-b-man", ip="198.18.56.10")
        device = self._make_device(
            db_session, serial="3856-MANUAL-NET", host_id=host_a.id,
        )
        token = f"{device.id}:11"
        lease = self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.MAINTENANCE.value,
            reason="manual_network_supply",
            holder="operator",
            fencing_token=token,
        )
        db_session.commit()

        caplog.set_level(logging.WARNING, logger=HEARTBEAT_LOGGER)
        assert self._beat(client, host_b.id, device.serial).status_code == 200

        db_session.expire_all()
        device = db_session.query(Device).filter(
            Device.serial == "3856-MANUAL-NET"
        ).one()
        lease = db_session.get(DeviceLease, lease.id)
        assert device.host_id == host_b.id
        assert device.status == "BUSY"
        assert lease.host_id == host_a.id
        assert lease.status == "ACTIVE"
        assert lease.fencing_token == token
        assert any(
            "maintenance_lease_host_mismatch" in r.getMessage()
            for r in caplog.records
        ), [r.getMessage() for r in caplog.records]

    def test_after_override_next_heartbeat_returns_online(
        self, client, db_session,
    ):
        """⑥ 欠账清偿/人工放行后下一拍心跳 → ONLINE。"""
        from backend.models.device_lease import DeviceLease
        from backend.models.enums import LeaseStatus, LeaseType
        from backend.services.device_return import (
            DEBT_LEASE_HOLDER,
            DEBT_LEASE_REASON,
            apply_override_sync,
        )

        host_a = self._make_host(db_session, host_id="3856-a-ov", ip="198.18.56.11")
        host_b = self._make_host(db_session, host_id="3856-b-ov", ip="198.18.56.12")
        device = self._make_device(
            db_session, serial="3856-DEBT-OV", host_id=host_a.id,
        )
        token = f"{device.id}:13"
        lease = self._make_lease(
            db_session,
            device=device,
            host_id=host_a.id,
            lease_type=LeaseType.MAINTENANCE.value,
            reason=DEBT_LEASE_REASON,
            holder=DEBT_LEASE_HOLDER,
            fencing_token=token,
        )
        self._make_open_debt(
            db_session, device_id=device.id, fencing_token=token,
        )
        db_session.commit()

        assert self._beat(client, host_b.id, device.serial).status_code == 200
        db_session.expire_all()
        device = db_session.query(Device).filter(
            Device.serial == "3856-DEBT-OV"
        ).one()
        assert device.host_id == host_b.id
        assert device.status == "BUSY"

        result = apply_override_sync(
            db_session,
            device_id=device.id,
            families=["power-cycle"],
            reason_category="flashed",
            evidence_ref="test:3856",
        )
        db_session.commit()
        assert result["ok"] is True
        assert result["debt_lease_released"] is True
        lease = db_session.get(DeviceLease, lease.id)
        assert lease.status == LeaseStatus.RELEASED.value

        assert self._beat(client, host_b.id, device.serial).status_code == 200
        db_session.expire_all()
        device = db_session.query(Device).filter(
            Device.serial == "3856-DEBT-OV"
        ).one()
        assert device.host_id == host_b.id
        assert device.status == "ONLINE"
