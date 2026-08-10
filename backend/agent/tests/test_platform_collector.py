"""PlatformCollector unit tests."""

from pathlib import Path

from backend.agent.aee.collector import get_collector_for_platform
from backend.agent.aee.collectors.mtk import MtkPlatformCollector


def test_get_collector_mtk():
    collector = get_collector_for_platform("MTK")
    assert isinstance(collector, MtkPlatformCollector)


def test_get_collector_unknown_falls_back_to_mtk():
    collector = get_collector_for_platform("UNKNOWN")
    assert isinstance(collector, MtkPlatformCollector)


def test_mtk_parse_metadata_from_exp_main(tmp_path):
    event_dir = tmp_path / "2026-01-01_12-00-00_KE"
    event_dir.mkdir()
    (event_dir / "__exp_main.txt").write_text(
        "Exception Class: Kernel (KE)\n",
        encoding="utf-8",
    )
    meta = MtkPlatformCollector().parse_metadata(event_dir)
    assert meta.event_type
