"""Publish repeatable inventory through the existing Agent heartbeat endpoint."""

import time

from tools.dev.fake_agent import build_heartbeat_payload, guard_target, post_json, require_secret

LONG_MODEL = "巡检模拟设备长名称-用于验证设备表格中名称截断与提示是否仍然可读"
DEVICES_PER_HOST = 30


def main() -> None:
    base = guard_target("http://127.0.0.1:8000", in_container=True)
    secret = require_secret()
    payloads = [
        build_heartbeat_payload(
            1,
            device_count=DEVICES_PER_HOST,
            host_ip=f"192.0.2.{11 + index}",
            host_name=f"ui-review-host-{index + 1}",
            serial_prefix=f"UIREVIEW{index + 1}",
        )
        for index in range(2)
    ]
    for host_idx, payload in enumerate(payloads):
        payload["devices"][0]["model"] = LONG_MODEL
        usb_slots: dict[str, list[str | None]] = {}
        for dev_idx, device in enumerate(payload["devices"]):
            device["temperature"] = device.pop("battery_temp") // 10
            device["disk_total"] = 128_000_000_000
            device["disk_used"] = 64_000_000_000 + (dev_idx % 5) * 8_000_000_000
            if host_idx == 0:
                slot = f"1-5.{dev_idx + 1}:1"
                if device["adb_connected"]:
                    usb_slots[device["serial"]] = [slot, "0e8d:201c"]
                else:
                    vid_pid = "0e8d:2046" if dev_idx % 2 == 0 else None
                    usb_slots[device["serial"]] = [slot, vid_pid]
        capacity_update: dict[str, object] = {
            "online_healthy_devices": DEVICES_PER_HOST - 1,
            "usb_device_count": DEVICES_PER_HOST,
        }
        if usb_slots:
            capacity_update["usb_power_slots"] = usb_slots
        payload["capacity"].update(capacity_update)

    while True:
        for payload in payloads:
            status, _ = post_json(base, secret, "/api/v1/heartbeat", payload, timeout=10)
            if status != 200:
                raise SystemExit(f"Fixture heartbeat failed: HTTP {status}")
        time.sleep(25)


if __name__ == "__main__":
    main()
