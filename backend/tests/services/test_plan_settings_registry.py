"""计划设置登记表框架。barrier 未设按现行环境回落，不写成无硬顶。"""
from __future__ import annotations

from backend.services.plan_parameter_projection import (
    load_plan_settings_document,
    validate_plan_settings_document,
)


def test_committed_registry_is_structurally_valid():
    document = load_plan_settings_document()
    assert document["schema_version"] == 1
    assert validate_plan_settings_document(document) == []
    by_scope: dict[str, list] = {}
    for entry in document["entries"]:
        by_scope.setdefault(entry["scope"], []).append(entry)
        assert entry["label"]
        assert any("\u4e00" <= ch <= "\u9fff" for ch in entry["label"] + entry["meaning"])
    assert len(by_scope["watcher"]) == 17
    barrier = next(
        item for item in by_scope["plan"] if item["path"] == ["barrier_max_wait_seconds"]
    )
    assert barrier["unset_state"] == "env_fallback"
    assert "STP_BARRIER_MAX_WAIT_SECONDS" in barrier["fallback_chain"]
    assert "1800" in barrier["fallback_chain"]
    assert "≤0" in barrier["fallback_chain"]
    assert barrier["unset_state"] != "unset_definite"
