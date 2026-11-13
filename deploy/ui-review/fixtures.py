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
    for payload in payloads:
        payload["devices"][0]["model"] = LONG_MODEL
        for device in payload["devices"]:
            device["temperature"] = device.pop("battery_temp") // 10
        payload["capacity"].update(
            online_healthy_devices=DEVICES_PER_HOST - 1,
            usb_device_count=DEVICES_PER_HOST,
        )

    while True:
        for payload in payloads:
            status, _ = post_json(base, secret, "/api/v1/heartbeat", payload, timeout=10)
            if status != 200:
                raise SystemExit(f"Fixture heartbeat failed: HTTP {status}")
        time.sleep(25)


if __name__ == "__main__":
    main()
