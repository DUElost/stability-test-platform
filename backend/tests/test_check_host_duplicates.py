"""#834: check_host_duplicates groups by ip/name column, not id."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.models.host import Host
from backend.scripts.check_host_duplicates import _find_duplicates, _group_key


def test_group_key_uses_ip_and_name_not_id():
    row = (10, "host-a", "192.0.2.1", "a.local", None)
    assert _group_key(Host.ip, row) == "192.0.2.1"
    assert _group_key(Host.name, row) == "host-a"


def test_group_key_rejects_unknown_column():
    with pytest.raises(ValueError, match="unsupported"):
        _group_key(Host.hostname, (1, "n", "192.0.2.1", "h", None))


def test_find_duplicates_merges_rows_sharing_ip():
    """Regression for #834: two hosts with same ip must share one group key."""
    db = MagicMock()
    # First execute: duplicate values query → single shared ip
    # Second execute: detail rows (id, name, ip, hostname, last_heartbeat)
    dup_result = MagicMock()
    dup_result.all.return_value = [("192.0.2.1",)]
    detail_result = MagicMock()
    detail_result.all.return_value = [
        (1, "old", "192.0.2.1", "old.local", None),
        (2, "new", "192.0.2.1", "new.local", None),
    ]
    db.execute.side_effect = [dup_result, detail_result]

    groups = _find_duplicates(db, Host.ip)
    assert list(groups.keys()) == ["192.0.2.1"]
    assert len(groups["192.0.2.1"]) == 2
    assert {r[0] for r in groups["192.0.2.1"]} == {1, 2}


def test_find_duplicates_merges_rows_sharing_name():
    db = MagicMock()
    dup_result = MagicMock()
    dup_result.all.return_value = [("dup-name",)]
    detail_result = MagicMock()
    detail_result.all.return_value = [
        (3, "dup-name", "192.0.2.10", None, None),
        (4, "dup-name", "192.0.2.11", None, None),
    ]
    db.execute.side_effect = [dup_result, detail_result]

    groups = _find_duplicates(db, Host.name)
    assert list(groups.keys()) == ["dup-name"]
    assert len(groups["dup-name"]) == 2
