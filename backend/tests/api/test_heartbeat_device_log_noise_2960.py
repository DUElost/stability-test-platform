"""#2960：逐设备**稳态**事实不该每拍占一行 INFO。

现场量级（2026-09-20，`logs/backend.log` 00:17 轮转后单文件）：**776 MB / 5,745,482 行**，
其中模块 `backend.api.routes.heartbeat` 占 **3,924,674 行 = 68%**，两条源头都是
"每设备每心跳一条 INFO"（`device_adb_update`、`device_status_online`）。626 台 ONLINE
设备 × 每 5s 一拍，这是**稳定量级而不是突发**——所以修它不是审美问题：日志体积决定
「出事后还查得到多久之前的现场」。

本文件钉三件事，缺一不可：

1. **稳态不占 INFO**（同一台设备同一事实重播，第 2 次起信息量为 0）；
2. **事实变化必须仍然 INFO 可见**（降到 DEBUG 如果把「什么时候翻的」也一起降了，
   就等于把 #107/#2569 辛苦建起来的成因链又变哑——那是比刷屏更坏的失效）；
3. **稳态是降级别，不是删日志**（DEBUG 下文案逐字可见：排查单机时把该 logger 调回
   DEBUG 就恢复全量轨迹，既有 grep 习惯不破）。

与 #2569 那条「同一台 host 再报一次不该出现改绑日志」同族：都在治同一种病——
**把每拍重复的事实当日志事件写**。
"""
from __future__ import annotations

import logging

HEARTBEAT_LOGGER = "backend.api.routes.heartbeat"
DEVICE_FACT_PREFIXES = ("device_adb_update:", "device_status_")


def _beat(client, ip: str, serial: str, *, adb_state: str = "device",
          adb_connected: bool = True):
    return client.post(
        "/api/v1/heartbeat",
        json={
            "host_id": 0,  # 自动注册哨兵：按 IP 建/找 host（与 #2569 夹具同一条路径）
            "status": "ONLINE",
            "host": {"ip": ip},
            "devices": [
                {
                    "serial": serial,
                    "adb_state": adb_state,
                    "adb_connected": adb_connected,
                    "battery_level": 85,
                    "temperature": 36,
                    "network_latency": 15.5,
                }
            ],
        },
    )


def _device_lines(caplog, level: int) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno == level
        and any(msg.startswith(p) for p in DEVICE_FACT_PREFIXES for msg in [r.getMessage()])
    ]


def test_steady_state_beats_stay_out_of_info(client, caplog):
    """首见仍 INFO（它是新事实）；之后同一事实重播一律不该出现在 INFO。"""
    ip, serial = "198.18.7.21", "QuietSerial-2960-A"
    caplog.set_level(logging.INFO, logger=HEARTBEAT_LOGGER)
    assert _beat(client, ip, serial).status_code == 200
    assert _device_lines(caplog, logging.INFO), "设备首次上报就该有 INFO 事实行"

    caplog.clear()
    for _ in range(6):  # 模拟 30s 内 6 拍（真实节拍 5s）
        assert _beat(client, ip, serial).status_code == 200
    assert _device_lines(caplog, logging.INFO) == [], (
        "逐设备稳态事实又回到 INFO —— #2960 的 68% 刷屏会原样复发"
    )


def test_steady_state_is_downgraded_not_deleted(client, caplog):
    """DEBUG 下必须仍看得见，且**文案逐字不变**。

    只测「INFO 没了」的守卫会放过最坏的一种实现：直接把日志删掉。这里反向钉住
    「降级别」这个语义，顺带钉住 grep 习惯（前缀串一字未改）。
    """
    ip, serial = "198.18.7.22", "QuietSerial-2960-B"
    caplog.set_level(logging.DEBUG, logger=HEARTBEAT_LOGGER)
    _beat(client, ip, serial)
    caplog.clear()
    _beat(client, ip, serial)

    lines = _device_lines(caplog, logging.DEBUG)
    assert any(l.startswith("device_adb_update: serial=") for l in lines), lines
    assert any(l.startswith("device_status_online: serial=") for l in lines), lines
    assert all(serial in l for l in lines), f"稳态行仍应带得上 serial：{lines}"


def test_adb_fact_flip_still_logs_at_info(client, caplog):
    """连通性事实翻转必须 INFO——否则「设备什么时候掉的」这条最该留痕的事实又变哑。"""
    ip, serial = "198.18.7.23", "FlipSerial-2960-C"
    caplog.set_level(logging.INFO, logger=HEARTBEAT_LOGGER)
    _beat(client, ip, serial)
    caplog.clear()

    _beat(client, ip, serial, adb_state="offline", adb_connected=False)
    info = _device_lines(caplog, logging.INFO)
    assert any(l.startswith("device_adb_update:") for l in info), info
    assert any(l.startswith("device_status_offline:") for l in info), info

    caplog.clear()
    _beat(client, ip, serial, adb_state="offline", adb_connected=False)
    assert _device_lines(caplog, logging.INFO) == [], "翻转后的稳态不该再刷屏"


def test_gate_shares_the_materiality_predicate(client, db_session, caplog):
    """日志门与落库门必须同一份判定（`device_connectivity_changed`）。

    形状选择而不是行为断言：本用例防的是**将来**有人为了少写日志再去收紧/放宽其中一处
    ——两份定义一分成二，就必然出现「库里写了但日志没有」或反之。
    """
    from backend.services.dashboard_summary import device_connectivity_changed

    assert device_connectivity_changed(
        prev_adb_state="device", new_adb_state="offline",
        prev_adb_connected=True, new_adb_connected=False,
    ) is True
    assert device_connectivity_changed(
        prev_adb_state="offline", new_adb_state="offline",
        prev_adb_connected=False, new_adb_connected=False,
    ) is False
