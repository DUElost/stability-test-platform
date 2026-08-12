"""PlatformCollector unit tests."""

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


def test_get_collector_unisoc_is_stub_only():
    """#220: keep UNISOC entry; do not implement collect/parse."""
    collector = get_collector_for_platform("UNISOC")
    assert isinstance(collector, UnisocPlatformCollector)
    assert collector.detect(lambda *_a, **_k: "unused", "serial") is False
    with pytest.raises(CollectorError, match="UNISOC"):
        collector.parse_metadata(Path("/tmp/no-such-event"))


def test_get_collector_qcom_is_stub_only():
    """#220: keep QCOM entry; do not implement collect/parse."""
    collector = get_collector_for_platform("QCOM")
    assert isinstance(collector, QcomPlatformCollector)
    assert collector.detect(lambda *_a, **_k: "unused", "serial") is False
    with pytest.raises(CollectorError, match="QCOM"):
        collector.parse_metadata(Path("/tmp/no-such-event"))


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
