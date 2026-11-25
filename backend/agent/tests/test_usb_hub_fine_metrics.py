"""S1：零权限 sysfs Hub 拓扑 + 1h 内核 USB 错误数值指标单测。"""
from __future__ import annotations

import threading

from backend.agent import device_discovery as dd
from backend.agent.capacity_reporter import compute_capacity
from backend.agent.contracts.kernel_usb_faults import (
    KERNEL_ERROR_COUNTER_KEYS,
    parse_kernel_usb_faults,
)
from backend.agent.kernel_usb_faults import KernelUsbWatch


def _mk_dev(root, name: str, dev_class: str, *, bound_iface: bool | None = None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "bDeviceClass").write_text(dev_class + "\n", encoding="utf-8")
    if bound_iface is not None:
        iface = root / f"{name}:1.0"
        iface.mkdir(parents=True, exist_ok=True)
        if bound_iface:
            (iface / "driver").mkdir(exist_ok=True)


class TestInspectUsbHubTopology:
    def test_returns_none_when_sysfs_unreadable(self, tmp_path):
        assert dd.inspect_usb_hub_topology(str(tmp_path / "missing")) is None

    def test_healthy_cascade_hubs_have_zero_unbound(self, tmp_path):
        (tmp_path / "usb1").mkdir()
        (tmp_path / "usb2").mkdir()
        _mk_dev(tmp_path, "1-5", "09", bound_iface=True)
        _mk_dev(tmp_path, "1-5.1", "09", bound_iface=True)
        _mk_dev(tmp_path, "1-5.1.1", "00", bound_iface=True)

        assert dd.inspect_usb_hub_topology(str(tmp_path)) == {
            "cascade_hubs": 2,
            "unbound_hubs": 0,
            "unbound_idle_bus_hubs": 0,
        }

    def test_distinguishes_idle_ss_bus_unbound_hub_from_active_bus_hub(self, tmp_path):
        """复现 .75/.60/.42 半枚举形态：Bus 2 最深级 Hub 缺 driver 且 Bus 2 无在用设备。"""
        (tmp_path / "usb1").mkdir()
        (tmp_path / "usb2").mkdir()
        # 在用侧 Bus 1：正常 Hub + 一台手机
        _mk_dev(tmp_path, "1-10", "09", bound_iface=True)
        _mk_dev(tmp_path, "1-10.1", "00", bound_iface=True)
        # 失效侧 Bus 2：顶层 Hub 已绑驱动，最深级 Hub 无 driver（Driver=[none]/0p），无非 Hub 设备
        _mk_dev(tmp_path, "2-5", "09", bound_iface=True)
        _mk_dev(tmp_path, "2-5.4", "09", bound_iface=False)

        assert dd.inspect_usb_hub_topology(str(tmp_path)) == {
            "cascade_hubs": 3,
            "unbound_hubs": 1,
            "unbound_idle_bus_hubs": 1,
        }

    def test_unbound_hub_on_active_peripheral_bus_not_counted_as_idle(self, tmp_path):
        _mk_dev(tmp_path, "1-5", "09", bound_iface=True)
        _mk_dev(tmp_path, "1-5.1", "00", bound_iface=True)
        _mk_dev(tmp_path, "1-5.2", "09", bound_iface=False)

        assert dd.inspect_usb_hub_topology(str(tmp_path)) == {
            "cascade_hubs": 2,
            "unbound_hubs": 1,
            "unbound_idle_bus_hubs": 0,
        }


class TestKernelUsbErrorCounters:
    def test_parse_counts_disconnect_reset_enum_and_hub_port_errors(self):
        lines = [
            "usb 2-5: USB disconnect, device number 3",
            "usb 1-5.2: reset high-speed USB device number 12 using xhci_hcd",
            "usb 2-5: device descriptor read/64, error -32",
            "usb 2-5: config failed, error -22",
            "hub 2-5:1.0: hub_ext_port_status failed (err = -110)",
            "hub 2-0:1.0: port 5 disabled by hub (EMI?), re-enabling...",
            "usb 2-5: device not accepting address 4, error -71",
            "usb 2-5: Maybe the USB cable is bad?",
        ]
        faults = parse_kernel_usb_faults(lines)
        assert faults.disconnects == 1
        assert faults.resets == 1
        assert faults.enum_errors == 2
        assert faults.hub_port_errors == 2
        assert faults.link_errors == 1
        assert faults.cable_suspect == 1

    def test_watch_window_summary_returns_none_until_channel_ok(self):
        done = threading.Event()

        def _unreadable(*, since=None):
            done.set()
            return None

        watch = KernelUsbWatch(scanner=_unreadable)
        assert watch.window_summary() is None
        watch.poll(usb_device_count=10)
        assert done.wait(timeout=2.0)
        # 等待 _scan_worker 写完状态
        for _ in range(50):
            if watch.channel_state() == "unavailable":
                break
            threading.Event().wait(0.01)
        assert watch.window_summary() is None

    def test_watch_window_summary_aggregates_all_keys_and_expires(self):
        now_wall = [10_000.0]
        now_mono = [100.0]
        samples = [
            parse_kernel_usb_faults([
                "usb 2-5: USB disconnect, device number 3",
                "hub 2-5:1.0: hub_ext_port_status failed (err = -110)",
                "usb 2-5: device descriptor read/64, error -32",
                "usb 1-5: reset high-speed USB device number 9 using xhci_hcd",
            ]),
            parse_kernel_usb_faults([]),
        ]
        idx = [0]
        done = threading.Event()

        def _scanner(*, since=None):
            res = samples[min(idx[0], len(samples) - 1)]
            idx[0] += 1
            done.set()
            return res

        watch = KernelUsbWatch(
            interval_seconds=60.0,
            window_seconds=3600.0,
            scanner=_scanner,
            monotonic=lambda: now_mono[0],
            wall_clock=lambda: now_wall[0],
        )
        watch.poll(usb_device_count=10)
        assert done.wait(timeout=2.0)
        for _ in range(50):
            if watch.window_summary() is not None:
                break
            threading.Event().wait(0.01)

        summary = watch.window_summary()
        assert summary is not None
        assert set(summary.keys()) == set(KERNEL_ERROR_COUNTER_KEYS)
        assert summary == {
            "link_errors": 0,
            "cable_suspect": 0,
            "disconnects": 1,
            "resets": 1,
            "enum_errors": 1,
            "hub_port_errors": 1,
        }

        # 超过 1 小时滑窗后计数归零
        now_wall[0] += 3601.0
        assert watch.window_summary() == {k: 0 for k in KERNEL_ERROR_COUNTER_KEYS}


class TestCapacityReporterPureObservation:
    def test_new_usb_fields_omitted_when_none_and_do_not_gate_slots(self):
        base = compute_capacity(
            active_job_count=0,
            active_device_count=0,
            online_healthy_devices=4,
            total_devices=4,
            system_stats={"cpu_load": 10, "ram_usage": 20, "disk_usage": {"writable_percent": 30}},
            mount_status={"/mnt/nfs": {"ok": True}},
        )
        assert "usb_hub_topology" not in base["capacity"]
        assert "usb_kernel_errors_1h" not in base["capacity"]

        with_fine = compute_capacity(
            active_job_count=0,
            active_device_count=0,
            online_healthy_devices=4,
            total_devices=4,
            system_stats={"cpu_load": 10, "ram_usage": 20, "disk_usage": {"writable_percent": 30}},
            mount_status={"/mnt/nfs": {"ok": True}},
            usb_hub_topology={"cascade_hubs": 5, "unbound_hubs": 1, "unbound_idle_bus_hubs": 1},
            usb_kernel_errors_1h={k: 3 for k in KERNEL_ERROR_COUNTER_KEYS},
        )
        assert with_fine["capacity"]["effective_slots"] == base["capacity"]["effective_slots"]
        assert with_fine["health"]["status"] == "HEALTHY"
        assert with_fine["capacity"]["usb_hub_topology"]["unbound_hubs"] == 1
        assert with_fine["capacity"]["usb_kernel_errors_1h"]["hub_port_errors"] == 3


def _mk_port(parent, name: str, *, disable: str = "0", oc: str = "0", state: str = "not attached", has_dev: bool = False):
    p = parent / name
    p.mkdir(parents=True, exist_ok=True)
    (p / "disable").write_text(disable + "\n", encoding="utf-8")
    (p / "over_current_count").write_text(oc + "\n", encoding="utf-8")
    (p / "state").write_text(state + "\n", encoding="utf-8")
    if has_dev:
        (p / "device").mkdir(exist_ok=True)


def _mk_pci(pci_root, bdf: str, pci_class: str, *, bound_driver: bool, enable: str = "1"):
    d = pci_root / bdf
    d.mkdir(parents=True, exist_ok=True)
    (d / "class").write_text(pci_class + "\n", encoding="utf-8")
    (d / "enable").write_text(enable + "\n", encoding="utf-8")
    if bound_driver:
        (d / "driver").mkdir(exist_ok=True)


class TestInspectUsbControllerHealth:
    def test_returns_none_when_usb_sysfs_missing(self, tmp_path):
        assert dd.inspect_usb_controller_health(str(tmp_path / "missing"), str(tmp_path / "pci")) is None

    def test_healthy_controller_and_ports_all_zero(self, tmp_path):
        usb_root = tmp_path / "usb"
        pci_root = tmp_path / "pci"
        _mk_pci(pci_root, "0000:00:14.0", "0x0c0330", bound_driver=True, enable="0")
        rh1 = usb_root / "usb1" / "1-0:1.0"
        _mk_port(rh1, "usb1-port1", disable="0", state="not attached")
        hub_iface = usb_root / "1-5:1.0"
        _mk_port(hub_iface, "1-5-port1", disable="0", state="configured", has_dev=True)

        # .63 形态：enable=0 但 driver 已绑定，不得误报 unbound_xhci_controllers
        assert dd.inspect_usb_controller_health(str(usb_root), str(pci_root)) == {
            "unbound_xhci_controllers": 0,
            "disabled_root_ports": 0,
            "disabled_hub_ports": 0,
            "over_current_ports": 0,
            "stuck_default_ports": 0,
        }

    def test_detects_unbound_xhci_disabled_ports_over_current_and_stuck_default(self, tmp_path):
        usb_root = tmp_path / "usb"
        pci_root = tmp_path / "pci"
        # .88 probe -110 形态：PCI 0x0c0330 缺少 driver 链接
        _mk_pci(pci_root, "0000:00:14.0", "0x0c0330", bound_driver=False, enable="0")
        # 非 xHCI PCI 设备忽略
        _mk_pci(pci_root, "0000:00:1f.0", "0x060100", bound_driver=False)

        # .73 HC died 形态：root hub 端口 disable=1
        rh1 = usb_root / "usb1" / "1-0:1.0"
        _mk_port(rh1, "usb1-port1", disable="1", state="not attached")
        _mk_port(rh1, "usb1-port2", disable="1", oc="2", state="not attached")

        # Hub 下游端口：VBUS 0000 off (disable=1) + .57 卡死 (state=default 且无 device)
        hub_iface = usb_root / "1-5.1:1.0"
        _mk_port(hub_iface, "1-5.1-port1", disable="1", state="not attached")
        _mk_port(hub_iface, "1-5.1-port2", disable="0", state="default", has_dev=False)
        _mk_port(hub_iface, "1-5.1-port3", disable="0", state="default", has_dev=True)

        assert dd.inspect_usb_controller_health(str(usb_root), str(pci_root)) == {
            "unbound_xhci_controllers": 1,
            "disabled_root_ports": 2,
            "disabled_hub_ports": 1,
            "over_current_ports": 1,
            "stuck_default_ports": 1,
        }

    def test_capacity_reporter_omits_controller_health_when_none_and_keeps_slots(self):
        base = compute_capacity(
            active_job_count=0,
            active_device_count=0,
            online_healthy_devices=4,
            total_devices=4,
            system_stats={"cpu_load": 10, "ram_usage": 20, "disk_usage": {"writable_percent": 30}},
            mount_status={"/mnt/nfs": {"ok": True}},
        )
        assert "usb_controller_health" not in base["capacity"]

        ctrl = {
            "unbound_xhci_controllers": 0,
            "disabled_root_ports": 2,
            "disabled_hub_ports": 0,
            "over_current_ports": 0,
            "stuck_default_ports": 1,
        }
        with_ctrl = compute_capacity(
            active_job_count=0,
            active_device_count=0,
            online_healthy_devices=4,
            total_devices=4,
            system_stats={"cpu_load": 10, "ram_usage": 20, "disk_usage": {"writable_percent": 30}},
            mount_status={"/mnt/nfs": {"ok": True}},
            usb_controller_health=ctrl,
        )
        assert with_ctrl["capacity"]["usb_controller_health"] == ctrl
        assert with_ctrl["capacity"]["effective_slots"] == base["capacity"]["effective_slots"]
