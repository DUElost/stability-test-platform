"""PlanRunTrigger.note → run_context.note (iteration C1, no DB column)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.api.routes.plans import PlanRunTrigger


def test_note_is_optional_and_stripped():
    payload = PlanRunTrigger(device_ids=[1], note="  batch-a  ")
    assert payload.note == "batch-a"


def test_blank_note_normalizes_to_none():
    assert PlanRunTrigger(device_ids=[1], note="   ").note is None
    assert PlanRunTrigger(device_ids=[1]).note is None


def test_note_max_length_500():
    PlanRunTrigger(device_ids=[1], note="x" * 500)
    with pytest.raises(ValidationError):
        PlanRunTrigger(device_ids=[1], note="x" * 501)


# ── PlanRunTrigger.wifi_pool_id → run_context.wifi_pool_id ────────────────
# 「执行前可选连接 WiFi」：不传 = 不连接（缺省）；传 = 用该资源池的凭据。
# 只收 pool_id、不收明文 ssid/password —— 凭据只存资源池一处。

def test_wifi_pool_id_defaults_to_none_meaning_do_not_connect():
    assert PlanRunTrigger(device_ids=[1]).wifi_pool_id is None


def test_wifi_pool_id_must_be_positive():
    assert PlanRunTrigger(device_ids=[1], wifi_pool_id=3).wifi_pool_id == 3
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            PlanRunTrigger(device_ids=[1], wifi_pool_id=bad)


def test_inline_credentials_are_rejected():
    """extra='forbid' 挡住明文凭据，避免每次 run 都把密码复制进 payload。"""
    with pytest.raises(ValidationError):
        PlanRunTrigger(device_ids=[1], ssid="office", password="pw")
