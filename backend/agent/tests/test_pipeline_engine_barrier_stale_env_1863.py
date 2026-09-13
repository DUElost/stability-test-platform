"""#1863：STP_BARRIER_PROGRESS_STALE_SECONDS 模块级解析护栏。"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 120.0),  # 未设置
        ("", 120.0),  # 空
        ("  ", 120.0),  # 空白
        ("30s", 120.0),  # 非法
        ("nan", 120.0),
        ("inf", 120.0),
        ("-inf", 120.0),
        ("-5", 120.0),  # 负
        ("0", 120.0),
        ("90", 90.0),  # 正常
        ("120.5", 120.5),
    ],
)
def test_parse_peer_progress_stale_seconds_guards(monkeypatch, raw, expected):
    from backend.agent.pipeline_engine import _parse_peer_progress_stale_seconds

    if raw is None:
        monkeypatch.delenv("STP_BARRIER_PROGRESS_STALE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("STP_BARRIER_PROGRESS_STALE_SECONDS", raw)
    assert _parse_peer_progress_stale_seconds() == expected
