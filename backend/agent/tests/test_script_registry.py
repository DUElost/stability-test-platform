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


def test_script_registry_ignores_legacy_aee_scripts_from_server(local_db, monkeypatch):
    payload = {
        "data": [
            {
                "id": 101,
                "name": "push_bundle",
                "version": "2.0.0",
                "script_type": "python",
                "nfs_path": "/mnt/storage/test-platform/scripts/resource/push_bundle/v2.0.0/push_bundle.py",
                "content_sha256": "b" * 64,
            },
            {
                "id": 201,
                "name": "scan_aee",
                "version": "1.0.0",
                "script_type": "python",
                "nfs_path": "/mnt/storage/test-platform/scripts/scan_aee/v1.0.0/scan_aee.py",
                "content_sha256": "c" * 64,
            },
        ]
    }

    monkeypatch.setattr(
        "backend.agent.registry.script_registry.requests.get",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    registry = ScriptRegistry(local_db, "http://server")
    registry.initialize()

    assert registry.resolve("push_bundle", "2.0.0").name == "push_bundle"
    with pytest.raises(Exception, match="not found"):
        registry.resolve("scan_aee", "1.0.0")
    assert "scan_aee::1.0.0" not in local_db.load_script_cache()


def test_script_registry_ignores_legacy_aee_scripts_from_sqlite_fallback(
    local_db, monkeypatch,
):
    local_db.save_script_cache({
        "push_bundle::2.0.0": {
            "script_id": 101,
            "name": "push_bundle",
            "version": "2.0.0",
            "script_type": "python",
            "nfs_path": "/cached/push_bundle.py",
            "content_sha256": "b" * 64,
        },
        "scan_aee::1.0.0": {
            "script_id": 201,
            "name": "scan_aee",
            "version": "1.0.0",
            "script_type": "python",
            "nfs_path": "/cached/scan_aee.py",
            "content_sha256": "c" * 64,
        },
    })

    def fake_get(url, headers=None, params=None, timeout=10):
        raise RuntimeError("server unavailable")

    monkeypatch.setattr("backend.agent.registry.script_registry.requests.get", fake_get)

    registry = ScriptRegistry(local_db, "http://server")
    registry.initialize()

    assert registry.resolve("push_bundle", "2.0.0").nfs_path == "/cached/push_bundle.py"
    with pytest.raises(Exception, match="not found"):
        registry.resolve("scan_aee", "1.0.0")


# ---- ADR-0051 Phase 2b：package_sha256 贯通 server → 内存 → sqlite → 回退 ----

def test_script_registry_carries_package_sha256_and_persists(local_db, monkeypatch):
    payload = _script_payload()
    payload["data"][0]["package_sha256"] = "d" * 64
    payload["data"].append({**payload["data"][0], "id": 102, "version": "2.0.1", "package_sha256": None})
    monkeypatch.setattr(
        "backend.agent.registry.script_registry.requests.get",
        lambda url, headers=None, params=None, timeout=10: FakeResponse(payload),
    )
    registry = ScriptRegistry(local_db, "http://server", "secret")
    registry.initialize()
    assert registry.resolve("push_bundle", "2.0.0").package_sha256 == "d" * 64
    assert registry.resolve("push_bundle", "2.0.1").package_sha256 is None
    cached = local_db.load_script_cache()
    assert cached["push_bundle::2.0.0"]["package_sha256"] == "d" * 64
    assert cached["push_bundle::2.0.1"]["package_sha256"] is None

    # 断网回退：sqlite 里的包 sha 原样回来
    def boom(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr("backend.agent.registry.script_registry.requests.get", boom)
    offline = ScriptRegistry(local_db, "http://server", "secret")
    offline.initialize()
    assert offline.resolve("push_bundle", "2.0.0").package_sha256 == "d" * 64


def test_local_db_upgrades_legacy_script_cache_without_package_column(tmp_path):
    """老 Agent 的 sqlite 没有 package_sha256 列：initialize 幂等 ALTER，旧行读回 None。"""
    import sqlite3

    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE script_cache (
            cache_key TEXT PRIMARY KEY, script_id INTEGER NOT NULL, name TEXT NOT NULL,
            version TEXT NOT NULL, script_type TEXT NOT NULL, nfs_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        INSERT INTO script_cache VALUES ('a::1', 1, 'a', '1', 'python', '/x/a.py', 'ff', 'now');
        """
    )
    con.commit()
    con.close()
    db = LocalDB()
    db.initialize(str(path))
    try:
        assert db.load_script_cache()["a::1"]["package_sha256"] is None
        db.update_script_cache("a::1", {"script_id": 1, "name": "a", "version": "1", "script_type": "python",
                                        "nfs_path": "/x/a.py", "content_sha256": "ff", "package_sha256": "e" * 64})
        assert db.load_script_cache()["a::1"]["package_sha256"] == "e" * 64
        db.initialize(str(path))  # 幂等：再 initialize 不报「列已存在」
    finally:
        db.close()
