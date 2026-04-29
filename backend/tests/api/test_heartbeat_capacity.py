"""Tests for heartbeat capacity protocol (ADR-0019 Phase 1).

Verifies backward compatibility: old heartbeat without capacity field
still works, and new capacity field is accepted.
"""

import pytest


class TestHeartbeatCapacityBackwardCompat:

    def test_heartbeat_without_capacity(self, client):
        resp = client.post(
            "/api/v1/heartbeat",
            json={"host_id": "capacity-test-1", "status": "ONLINE"},
        )
        # 422 = pydantic validation error (capacity absence should NOT cause this)
        assert resp.status_code != 422, (
            f"Expected no 422, got {resp.status_code}: {resp.text}"
        )

    def test_heartbeat_with_capacity(self, client):
        resp = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": "capacity-test-2",
                "status": "ONLINE",
                "capacity": {
                    "available_slots": 1,
                    "max_concurrent_jobs": 4,
                    "online_healthy_devices": 3,
                },
            },
        )
        assert resp.status_code != 422, (
            f"Expected no 422 for valid capacity, got {resp.status_code}: {resp.text}"
        )

    def test_heartbeat_capacity_string_ignored(self, client):
        resp = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": "capacity-test-3",
                "status": "ONLINE",
                "capacity": {"max_concurrent_jobs": "abc"},
            },
        )
        assert resp.status_code != 422, (
            f"Expected no 422 for string max_concurrent_jobs, "
            f"got {resp.status_code}: {resp.text}"
        )

    def test_heartbeat_capacity_extra_keys(self, client):
        resp = client.post(
            "/api/v1/heartbeat",
            json={
                "host_id": "capacity-test-4",
                "status": "ONLINE",
                "capacity": {"cpu_usage": 0.75, "mem_usage": 0.60},
            },
        )
        assert resp.status_code != 422, (
            f"Expected no 422 for extra capacity keys, "
            f"got {resp.status_code}: {resp.text}"
        )
