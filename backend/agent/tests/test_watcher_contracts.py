# -*- coding: utf-8 -*-
"""#806：log_signal 契约白名单回归（source / category）。

此前 `reconciler_rollback` 不在 source 白名单 → reconciler 自关闭的可见性
信号 emit 必抛 ContractViolation 且被 except 吞掉（通道 100% 是死的）。
category `UNIVIEW` 与 LocalDB `get_state/set_state` 已先行修复（#1043），
本文件把三处形态一起钉住。
"""

from __future__ import annotations

import pytest

from backend.agent.watcher.contracts import ContractViolation, validate_log_signal


def _envelope(**overrides):
    envelope = {
        "job_id": 1,
        "seq_no": 1,
        "host_id": "host-1",
        "device_serial": "SERIAL-1",
        "fencing_token": "tok",
        "agent_instance_id": "inst",
        "category": "AEE",
        "source": "inotifyd",
        "path_on_device": "/sdcard/x",
        "detected_at": "2026-09-11T00:00:00+00:00",
    }
    envelope.update(overrides)
    return envelope


def test_reconciler_rollback_source_is_accepted():
    """reconciler 自关闭信号（source=reconciler_rollback）必须过契约校验。"""
    result = validate_log_signal(_envelope(source="reconciler_rollback"))
    assert result["source"] == "reconciler_rollback"


def test_uniview_category_is_accepted():
    """UNISOC UNIVIEW category 在白名单（#806 子项三已修，锁定形态）。"""
    result = validate_log_signal(
        _envelope(category="UNIVIEW", source="reconciler")
    )
    assert result["category"] == "UNIVIEW"


def test_unknown_source_is_rejected():
    with pytest.raises(ContractViolation):
        validate_log_signal(_envelope(source="mystery_channel"))
