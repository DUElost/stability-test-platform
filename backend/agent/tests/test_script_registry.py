"""Agent ScriptRegistry tests."""

import pytest

from backend.agent.registry.local_db import LocalDB
from backend.agent.registry.script_registry import ScriptRegistry


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def local_db(tmp_path):
    db = LocalDB()
    db.initialize(str(tmp_path / "agent.db"))
    yield db
    db.close()


def _script_payload():
    return {
        "data": [
            {
                "id": 101,
                "name": "push_bundle",
                "version": "2.0.0",
                "script_type": "python",
                "nfs_path": "/mnt/storage/test-platform/scripts/resource/push_bundle/v2.0.0/push_bundle.py",
                "content_sha256": "b" * 64,
            }
        ]
    }


def test_script_registry_loads_from_server_and_writes_sqlite(local_db, monkeypatch):
    def fake_get(url, headers=None, params=None, timeout=10):
        assert url == "http://server/api/v1/scripts"
        assert headers == {"X-Agent-Secret": "secret"}
        assert params == {"is_active": True}
        return FakeResponse(_script_payload())

    monkeypatch.setattr("backend.agent.registry.script_registry.requests.get", fake_get)

    registry = ScriptRegistry(local_db, "http://server", "secret")
    registry.initialize()

    entry = registry.resolve("push_bundle", "2.0.0")
    assert entry.script_id == 101
    assert entry.name == "push_bundle"
    assert entry.version == "2.0.0"
    assert registry.version
    assert local_db.load_script_cache()["push_bundle::2.0.0"]["content_sha256"] == "b" * 64


def test_script_registry_falls_back_to_sqlite(local_db, monkeypatch):
    local_db.save_script_cache({
        "push_bundle::2.0.0": {
            "script_id": 101,
            "name": "push_bundle",
            "version": "2.0.0",
            "script_type": "python",
            "nfs_path": "/cached/push_bundle.py",
            "content_sha256": "b" * 64,
        }
    })

    def fake_get(url, headers=None, params=None, timeout=10):
        raise RuntimeError("server unavailable")

    monkeypatch.setattr("backend.agent.registry.script_registry.requests.get", fake_get)

    registry = ScriptRegistry(local_db, "http://server")
    registry.initialize()

    entry = registry.resolve("push_bundle", "2.0.0")
    assert entry.nfs_path == "/cached/push_bundle.py"


def test_script_registry_resolve_rejects_missing_version(local_db, monkeypatch):
    monkeypatch.setattr(
        "backend.agent.registry.script_registry.requests.get",
        lambda *args, **kwargs: FakeResponse(_script_payload()),
    )

    registry = ScriptRegistry(local_db, "http://server")
    registry.initialize()

    with pytest.raises(Exception, match="version mismatch|not found"):
        registry.resolve("push_bundle", "1.0.0")
