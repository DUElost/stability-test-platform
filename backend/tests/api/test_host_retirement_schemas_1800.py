"""#1800 / ADR-0038 ①：退役 schema 契约。

- `HostOut` 交付面：退役四列 + 身份当前值（D1/D4/D6-(a)）；
- `HostRetireIn` / `HostUnretireIn`：`retire_reason` 必填且 min_length=1
  （ADR D2 审计 who/when/reason）。

端点行为（Cordon 前置、审计 fail-closed）属 ② #1801，不在本文件。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.api.schemas.host import HostOut, HostRetireIn, HostUnretireIn
from backend.models.host import Host


def test_hostout_defaults_retirement_and_identity_to_none():
    out = HostOut.model_validate(Host(id="h1", hostname="h1", status="ONLINE", watcher_admin_active=True))

    assert out.retired_at is None
    assert out.retired_by is None
    assert out.retire_reason is None
    assert out.retire_alerted_at is None
    assert out.boot_id is None
    assert out.agent_instance_id is None


def test_hostout_carries_retired_values_and_identity():
    host = Host(
        id="h1",
        hostname="h1",
        status="ONLINE",
        watcher_admin_active=True,
        retired_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        retired_by="admin",
        retire_reason="样机报废",
        boot_id="boot-abc",
        last_agent_instance_id="agent-xyz",
    )

    out = HostOut.model_validate(host)

    assert out.retired_at == datetime(2026, 9, 13, tzinfo=timezone.utc)
    assert out.retired_by == "admin"
    assert out.retire_reason == "样机报废"
    assert out.boot_id == "boot-abc"
    assert out.agent_instance_id == "agent-xyz"


@pytest.mark.parametrize("schema", [HostRetireIn, HostUnretireIn])
@pytest.mark.parametrize("payload", [{}, {"retire_reason": ""}])
def test_reason_is_required_and_non_empty(schema, payload):
    with pytest.raises(ValidationError):
        schema(**payload)


@pytest.mark.parametrize("schema", [HostRetireIn, HostUnretireIn])
def test_reason_accepts_non_empty(schema):
    assert schema(retire_reason="维修更换").retire_reason == "维修更换"
