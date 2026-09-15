"""PlatformCollector unit tests."""

import json
from pathlib import Path

import pytest

from backend.agent.aee.collector import CollectorError, get_collector_for_platform
from backend.agent.aee.collectors.mtk import MtkPlatformCollector
from backend.agent.aee.collectors.qcom import QcomPlatformCollector
from backend.agent.aee.collectors.unisoc import UnisocPlatformCollector


def test_get_collector_mtk():
    collector = get_collector_for_platform("MTK")
    assert isinstance(collector, MtkPlatformCollector)


def test_get_collector_unknown_falls_back_to_mtk():
    collector = get_collector_for_platform("UNKNOWN")
    assert isinstance(collector, MtkPlatformCollector)


def _write_unievent_info(event_dir: Path, *lines: dict) -> None:
    """真机形状夹具：JSONL（A 设备头 / B 元数据 / C 发生行由调用方给出）。"""
    event_dir.mkdir()
    (event_dir / "unievent_info").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines),
        encoding="utf-8",
    )


def test_get_collector_unisoc_parses_unievent_info(tmp_path):
    """真机形状（#2083）：A 设备头 + B 元数据 + C 发生行 → 解析出事件。"""
    collector = get_collector_for_platform("UNISOC")
    assert isinstance(collector, UnisocPlatformCollector)
    event_dir = tmp_path / "JE.103000004"
    _write_unievent_info(
        event_dir,
        {"sn": "UNI-1", "software_version": "MyOS16.0.1_Z2581_GEN_AF",
         "soc_model": "UMS9230E", "event_count": "1"},
        {"event_id": "103000004", "event_type": "FAULT", "event_level": "GENERAL",
         "event_name": "system_server_crash"},
        {"kick_datetime": "2026-09-08_06:59:12.031", "pid": "23847",
         "proc": "com.example.app", "tag": "system_app_crash"},
    )
    meta = collector.parse_metadata(event_dir)
    assert meta.event_type == "UNIVIEW"
    assert meta.event_subtype == "system_server_crash"
    assert meta.package_name == "com.example.app"


def test_unisoc_reboot_normalboot_only_is_not_reportable(tmp_path):
    """#2083：Reboot 目录有 meta 行、发生行全部 normalboot → 不可上报。

    反例：把 B 类元数据行也算「事件行」时，该目录会被 emit 成假异常
    （真机 signal 'Boot Category'，aee_ts=None）并自动上送。
    """
    event_dir = tmp_path / "Reboot.103000002"
    _write_unievent_info(
        event_dir,
        {"sn": "UNI-1", "soc_model": "UMS9230E", "event_count": "NA"},
        {"event_id": "103000002", "event_type": "FAULT", "event_level": "GENERAL",
         "event_name": "Boot Category"},
        {"kick_datetime": "2026-08-11_19:50:25.382", "event_time": 1786470625382,
         "reboot_reason": "normalboot"},
        {"kick_datetime": "2026-08-24_17:15:35.391", "event_time": 1787562935391,
         "reboot_reason": "normalboot"},
    )
    with pytest.raises(CollectorError, match="normalboot-only"):
        UnisocPlatformCollector().parse_metadata(event_dir)


def test_unisoc_reboot_abnormal_reason_still_reports(tmp_path):
    """#2083 反例保护：异常 reboot_reason（kernel_crash）仍须上报。"""
    event_dir = tmp_path / "Reboot.103000002"
    _write_unievent_info(
        event_dir,
        {"sn": "UNI-1", "soc_model": "UMS9230E", "event_count": "1"},
        {"event_id": "103000002", "event_type": "FAULT", "event_level": "GENERAL",
         "event_name": "Boot Category"},
        {"kick_datetime": "2026-09-08_06:00:00.000", "event_time": 1786470625382,
         "reboot_reason": "kernel_crash"},
    )
    meta = UnisocPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "UNIVIEW"
    assert meta.event_subtype == "Boot Category"
    assert meta.device_timestamp_raw == "2026-09-08_06:00:00.000"


def test_unisoc_legacy_filename_still_parses(tmp_path):
    """兼容读取旧文件名 `unievent_info.json`（真机从未观测到，仅为不静默失败保留）。"""
    event_dir = tmp_path / "evt-legacy"
    event_dir.mkdir()
    (event_dir / "unievent_info.json").write_text(
        '{"kick_datetime": "2026-09-08_06:59:12.031", "proc": "com.example.app"}',
        encoding="utf-8",
    )
    meta = UnisocPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "UNIVIEW"
    assert meta.package_name == "com.example.app"


def test_get_collector_qcom_is_stub_only():
    """#220: keep QCOM entry; do not implement collect/parse."""
    collector = get_collector_for_platform("QCOM")
    assert isinstance(collector, QcomPlatformCollector)
    with pytest.raises(CollectorError, match="QCOM"):
        collector.parse_metadata(Path("/tmp/no-such-event"))


def test_collector_protocol_declares_no_detect():
    """R4-a a1（2026-09-15 裁决）：协议不得再声明 ``detect()``。

    平台判定的唯一权威是 ``device_platform.detect_device_platform``；``detect()``
    曾是"定义了却从不调用"的第三种状态，删除后不得以未接线形态复活。

    裁决依据：docs/notes/architecture/2026-09-15-adr0032-v08-platform-routing-revision.md §R4-a。
    """
    from backend.agent.aee.collector import PlatformCollector

    assert not hasattr(PlatformCollector, "detect")
    for cls in (MtkPlatformCollector, UnisocPlatformCollector, QcomPlatformCollector):
        assert not hasattr(cls, "detect"), f"{cls.__name__} 仍在声明 detect()"


def test_mtk_parse_metadata_from_exp_main(tmp_path):
    event_dir = tmp_path / "2026-01-01_12-00-00_KE"
    event_dir.mkdir()
    (event_dir / "__exp_main.txt").write_text(
        "Exception Class: Kernel (KE)\n",
        encoding="utf-8",
    )
    meta = MtkPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "KE"
    assert meta.event_subtype == "KE"


def test_mtk_parse_metadata_from_zz_internal(tmp_path):
    event_dir = tmp_path / "db.00.JE"
    event_dir.mkdir()
    (event_dir / "ZZ_INTERNAL").write_text(
        "Java (JE),f1,f2,f3,f4,f5,f6,com.example.app,",
        encoding="utf-8",
    )
    meta = MtkPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "JE"
    assert meta.event_subtype == "JE"
    assert meta.package_name == "com.example.app"


def test_mtk_parse_metadata_falls_back_to_dirname(tmp_path):
    event_dir = tmp_path / "db.03.ANR"
    event_dir.mkdir()
    meta = MtkPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "ANR"
    assert meta.event_subtype == "ANR"


def test_mtk_parse_metadata_unknown_without_clues(tmp_path):
    event_dir = tmp_path / "db.99.misc"
    event_dir.mkdir()
    meta = MtkPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type == "UNKNOWN"
