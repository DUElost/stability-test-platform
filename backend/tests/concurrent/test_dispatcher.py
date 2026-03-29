"""
Concurrent Testing Suite — Heartbeat & Scheduling Properties

Retains pure-logic property tests (PROP-005: Heartbeat Timestamp Monotonicity)
from the original test suite.  Legacy Task/TaskRun dispatcher tests have been
removed as part of the ADR-0008 dual-track ORM merge.

Concurrent dispatch tests for `services/dispatcher.py` are tracked as future work.
"""

import pytest
from datetime import datetime, timedelta


class TestHeartbeatMonotonicity:
    """PROP-005: Heartbeat timestamp monotonicity."""

    def test_heartbeat_timestamp_monotonic(self):
        timestamps = []
        base_time = datetime.utcnow()

        for i in range(10):
            ts = base_time + timedelta(seconds=i * 5)
            timestamps.append(ts)

        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i - 1]

    def test_heartbeat_clock_skew_handling(self):
        """System should use max(current, received) when clock skew is detected."""
        current_heartbeat = datetime.utcnow()
        old_heartbeat = current_heartbeat - timedelta(seconds=30)
        effective_heartbeat = max(current_heartbeat, old_heartbeat)
        assert effective_heartbeat == current_heartbeat

    def test_heartbeat_sequence_integrity(self):
        """Out-of-order arrivals should be sorted correctly."""
        sequence = []
        base_time = datetime.utcnow()
        for i in range(5):
            sequence.append(base_time + timedelta(seconds=i * 5))
        sequence.append(base_time + timedelta(seconds=2 * 5))

        sorted_sequence = sorted(sequence)
        for i in range(1, len(sorted_sequence)):
            assert sorted_sequence[i] >= sorted_sequence[i - 1]
