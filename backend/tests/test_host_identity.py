"""Unit tests for IP-derived host id helpers."""

from backend.core.host_identity import allocate_host_id, ip_to_host_id


def test_ip_to_host_id_ipv4():
    assert ip_to_host_id("172.21.9.6") == "172-21-9-6"
    assert ip_to_host_id(" 10.0.0.1 ") == "10-0-0-1"


def test_ip_to_host_id_rejects_non_ipv4():
    assert ip_to_host_id(None) is None
    assert ip_to_host_id("") is None
    assert ip_to_host_id("not-an-ip") is None
    assert ip_to_host_id("::1") is None


def test_allocate_host_id_prefers_ip():
    assert allocate_host_id("172.21.9.6") == "172-21-9-6"


def test_allocate_host_id_collision_adds_suffix():
    taken = {"172-21-9-6"}
    result = allocate_host_id("172.21.9.6", exists=taken.__contains__)
    assert result.startswith("172-21-9-6-")
    assert result != "172-21-9-6"


def test_allocate_host_id_fallback_without_ip():
    result = allocate_host_id(None)
    assert result.startswith("auto-")
    assert len(result) == len("auto-") + 12
