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
