"""#2962 A / ADR-0057 D6·E4：设备陈旧度与退役建议的纯函数口径。

陈旧度是**现算**派生（不落库、可逆）：`OFFLINE` 且 `last_seen` 早于 7 天
（或为空）⇒ 陈旧；退役建议（E4）在陈旧基础上要求最后上报早于 30 天，且
`last_seen` 为空**不给建议**（「超过 30 天」无从度量，宁可少提示）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.core.device_lifecycle import (
    RETIRE_SUGGEST_AFTER_DAYS,
    STALE_AFTER_DAYS,
    is_retire_suggested,
    is_stale,
)

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=timezone.utc)


class TestStaleBoundary:
    def test_just_inside_window_is_not_stale(self):
        assert is_stale("OFFLINE", _NOW - timedelta(days=6, hours=23), now=_NOW) is False

    def test_older_than_threshold_is_stale(self):
        assert is_stale(
            "OFFLINE", _NOW - timedelta(days=STALE_AFTER_DAYS) - timedelta(seconds=1),
            now=_NOW,
        ) is True

    def test_exactly_at_threshold_is_not_stale(self):
        # 判据是 `< cutoff`（严格早于），正好 7 天不算陈旧——边界只在一个方向
        assert is_stale("OFFLINE", _NOW - timedelta(days=STALE_AFTER_DAYS), now=_NOW) is False

    def test_missing_last_seen_is_stale(self):
        assert is_stale("OFFLINE", None, now=_NOW) is True

    def test_online_busy_error_never_stale(self):
        # 只有 OFFLINE 会陈旧：ONLINE/BUSY/ERROR 不是「库存沉积」形态
        for status in ("ONLINE", "BUSY", "ERROR"):
            assert is_stale(status, _NOW - timedelta(days=90), now=_NOW) is False

    def test_naive_last_seen_treated_as_utc(self):
        naive = (_NOW - timedelta(days=8)).replace(tzinfo=None)
        assert is_stale("OFFLINE", naive, now=_NOW) is True


class TestRetireSuggestion:
    def test_29_days_no_suggestion(self):
        assert is_retire_suggested(
            "OFFLINE", _NOW - timedelta(days=29), now=_NOW,
        ) is False

    def test_31_days_suggests_retirement(self):
        assert is_retire_suggested(
            "OFFLINE", _NOW - timedelta(days=RETIRE_SUGGEST_AFTER_DAYS + 1), now=_NOW,
        ) is True

    def test_null_last_seen_is_stale_but_not_suggested(self):
        """空 last_seen 算陈旧（默认隐藏），但不给退役建议——阈值无从度量。"""
        assert is_stale("OFFLINE", None, now=_NOW) is True
        assert is_retire_suggested("OFFLINE", None, now=_NOW) is False

    def test_online_never_suggested(self):
        assert is_retire_suggested(
            "ONLINE", _NOW - timedelta(days=90), now=_NOW,
        ) is False

    def test_fresh_offline_not_suggested(self):
        assert is_retire_suggested("OFFLINE", _NOW - timedelta(hours=2), now=_NOW) is False
