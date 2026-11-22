"""B10 U3 — transfer-permits HTTP 端点契约。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from backend.core.settings.transfer import reset_transfer_permit_settings_cache
from backend.main import app
from backend.services import transfer_permit as tp


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expiry: dict[str, float] = {}
        self.now = time.monotonic()

    def _purge_if_expired(self, key: str) -> None:
        exp = self.expiry.get(key)
        if exp is not None and self.now >= exp:
            self.store.pop(key, None)
            self.expiry.pop(key, None)

    def _exists(self, key: str) -> int:
        self._purge_if_expired(key)
        return 1 if key in self.store else 0

    def close(self) -> None:
        return None

    def eval(self, script: str, numkeys: int, *args):  # noqa: ANN001
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "TRANSFER_PERMIT_ACQUIRE" in script:
            holders = keys[0]
            max_hosts = int(argv[0])
            token = str(argv[1])
            host_id = str(argv[2])
            ttl = int(argv[3])
            slot_prefix = str(argv[4])
            members = set(self.sets.get(holders, set()))
            for m in list(members):
                if self._exists(slot_prefix + m) == 0:
                    members.discard(m)
            self.sets[holders] = members
            slot = slot_prefix + token
            current = self.store.get(slot) if self._exists(slot) else None
            if current is not None:
                if current != host_id:
                    return [0, -1]
                self.expiry[slot] = self.now + ttl
                members.add(token)
                self.sets[holders] = members
                return [1, len(members)]
            count = len(members)
            if count >= max_hosts:
                return [0, count]
            self.store[slot] = host_id
            self.expiry[slot] = self.now + ttl
            members.add(token)
            self.sets[holders] = members
            return [1, count + 1]
        if "TRANSFER_PERMIT_RENEW" in script:
            slot, holders = keys[0], keys[1]
            expected_host, ttl, token = str(argv[0]), int(argv[1]), str(argv[2])
            if self._exists(slot) and self.store.get(slot) == expected_host:
                self.expiry[slot] = self.now + ttl
                self.sets.setdefault(holders, set()).add(token)
                return 1
            return 0
        if "TRANSFER_PERMIT_RELEASE" in script:
            slot, holders = keys[0], keys[1]
            expected_host, token = str(argv[0]), str(argv[1])
            if self._exists(slot) and self.store.get(slot) == expected_host:
                self.store.pop(slot, None)
                self.expiry.pop(slot, None)
                self.sets.setdefault(holders, set()).discard(token)
                return 1
            return 0
        raise AssertionError(script[:40])


_AGENT_SECRET = "test-agent-secret-b10-u3"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("AGENT_SECRET", _AGENT_SECRET)
    monkeypatch.setenv("STP_FIRMWARE_TRANSFER_MAX_HOSTS", "2")
    reset_transfer_permit_settings_cache()
    fake = _FakeRedis()
    tp.configure_transfer_permit_client(fake)
    with TestClient(app) as c:
        yield c
    tp.reset_transfer_permit_for_tests()
    reset_transfer_permit_settings_cache()


def _post(client: TestClient, action: str, body: dict):
    return client.post(
        f"/api/v1/agent/transfer-permits/{action}",
        json=body,
        headers={"X-Agent-Secret": _AGENT_SECRET},
    )


def test_acquire_renew_release_happy_path(client):
    r = _post(
        client,
        "acquire",
        {"host_id": "h1", "lease_token": "tok-a", "pool": "firmware_nfs"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["acquired"] is True
    assert data["max_hosts"] == 2

    renew = _post(
        client,
        "renew",
        {"host_id": "h1", "lease_token": "tok-a", "pool": "firmware_nfs"},
    )
    assert renew.status_code == 200
    assert renew.json()["data"]["renewed"] is True

    release = _post(
        client,
        "release",
        {"host_id": "h1", "lease_token": "tok-a", "pool": "firmware_nfs"},
    )
    assert release.status_code == 200
    assert release.json()["data"]["released"] is True


def test_acquire_returns_false_when_full(client):
    assert _post(client, "acquire", {"host_id": "h1", "lease_token": "t1"}).json()["data"][
        "acquired"
    ]
    assert _post(client, "acquire", {"host_id": "h2", "lease_token": "t2"}).json()["data"][
        "acquired"
    ]
    third = _post(client, "acquire", {"host_id": "h3", "lease_token": "t3"})
    assert third.status_code == 200
    assert third.json()["data"]["acquired"] is False


def test_mismatch_release_is_409(client):
    _post(client, "acquire", {"host_id": "h1", "lease_token": "t1"})
    bad = _post(client, "release", {"host_id": "h1", "lease_token": "wrong"})
    assert bad.status_code == 409
    assert bad.json()["detail"]["code"] == "LEASE_TOKEN_MISMATCH"
