"""#217: PRUNED events remain extractable (remote_path on CIFS)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from backend.models.device_log_event import DeviceLogEvent
from backend.models.enums import EventState
from backend.services.device_log_event import (
    count_pending_upload_events,
    list_remote_paths_for_extract,
)


def test_pruned_events_count_as_upload_complete_and_list_for_extract(
    db_session, sample_plan_run, sample_host, sample_device,
):
    remote = f"/mnt/stp-aee/devices/{sample_plan_run.id}/db.01.ANR"
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        detected_at=datetime.now(timezone.utc),
        state=EventState.PRUNED.value,
        local_path="/mnt/hdd/aee_events/gone",
        remote_path=remote,
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.commit()

    assert count_pending_upload_events(db_session, sample_plan_run.id) == 0
    assert remote in list_remote_paths_for_extract(db_session, sample_plan_run.id)


def test_pending_count_defaults_to_filter_model(
    db_session, sample_plan_run, sample_host, sample_device, monkeypatch,
):
    """ADR-0028 方案 A：默认过滤模型下，LOCAL（未被 upload_task 标记）不计入 pending。"""
    monkeypatch.delenv("STP_EVENT_UPLOADER_CONTINUOUS", raising=False)
    db_session.add(DeviceLogEvent(
        id=uuid4(),
        serial=sample_device.serial,
        platform="MTK",
        event_type="ANR",
        detected_at=datetime.now(timezone.utc),
        state=EventState.LOCAL.value,
        local_path="/mnt/hdd/aee_events/local-only",
        plan_run_id=sample_plan_run.id,
        host_id=sample_host.id,
    ))
    db_session.commit()

    assert count_pending_upload_events(db_session, sample_plan_run.id) == 0
