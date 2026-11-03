"""#1520 垂直切片：Agent DLE ingest 服务层直测。"""

from __future__ import annotations

import pytest
from backend.services.errors import ServiceError

from backend.models.enums import EventState
from backend.services.agent_device_log_events import (
    DeviceLogEventBatchIn,
    _ALLOWED_TRANSITIONS,
    _EXTRACTABLE_STATES,
    _parse_iso_dt,
    ingest_agent_device_log_events,
)


class TestParseIsoDt:
    def test_parses_zulu(self):
        dt = _parse_iso_dt("2026-09-17T01:02:03Z", "detected_at")
        assert dt.year == 2026
        assert dt.tzinfo is not None

    def test_rejects_garbage(self):
        with pytest.raises(ServiceError) as exc:
            _parse_iso_dt("not-a-date", "detected_at")
        assert exc.value.status == 400


class TestAllowedTransitions:
    def test_pull_failed_on_non_terminal(self):
        pull = EventState.PULL_FAILED.value
        for state, allowed in _ALLOWED_TRANSITIONS.items():
            if state in _EXTRACTABLE_STATES:
                assert pull not in allowed
            else:
                assert pull in allowed


class TestIngestGuards:
    @pytest.mark.asyncio
    async def test_empty_batch_returns_zero(self):
        out = await ingest_agent_device_log_events(
            db=None,  # type: ignore[arg-type]
            payload=DeviceLogEventBatchIn(events=[]),
        )
        assert out == {"upserted": 0, "total": 0}


def test_shadow_report_and_companion_are_invisible_to_legacy_manifest_reader(tmp_path):
    from backend.agent.upload_manager import UploadManager
    from backend.services.dedup_scan import _read_manifest_shards

    source = tmp_path / "Result_test_org.xls"
    source.write_bytes(b"report bytes are copied unchanged")
    nfs = tmp_path / "nfs"
    uploader = UploadManager()
    uploader.configure(nfs_root=str(nfs))
    uploader.upload_scan_report(42, "h", str(source), platform_subdir="mtk")
    legacy = _read_manifest_shards(nfs / "_meta/42", 42)
    uploader.upload_scan_report(42, "h", str(source), platform_subdir="mtk",
                                fact_round_id=9, mapping={"rows": []})
    assert _read_manifest_shards(nfs / "_meta/42", 42) == legacy
    assert len(legacy) == 1
    assert legacy[0]["file_key"] == "mtk/h_Result_test_org.xls"
    assert list((nfs / "dedup/42/mtk").glob("*.xls"))[0].read_bytes() == source.read_bytes()
