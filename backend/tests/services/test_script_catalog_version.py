"""控制面与 Agent 的脚本目录版本算法必须**逐字节一致**。

不一致的后果不是报错，而是 `scripts_outdated` 永远为真：每次心跳都让全部
Agent 重新拉一遍目录。所以这里不"参考实现"，而是**直接 import Agent 的
`ScriptRegistry._compute_version`** 做对拍。
"""

import pytest

from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
from backend.models.script import Script
from backend.services.script_catalog_version import (
    catalog_digest,
    compute_script_catalog_version,
)


def _agent_compute_version(entries):
    """调用 Agent 的真实实现，而不是复制一份公式。"""
    from backend.agent.registry.script_registry import ScriptEntry, ScriptRegistry

    registry = ScriptRegistry.__new__(ScriptRegistry)
    registry._cache = {
        f"{name}::{version}": ScriptEntry(
            script_id=0, name=name, version=version,
            script_type="python", nfs_path="", content_sha256=sha,
        )
        for name, version, sha in entries
    }
    return registry._compute_version()


@pytest.mark.parametrize("entries", [
    [],
    [("check_device", "1.0.0", "aa")],
    [("monkey_setup", "2.0.0", "bb"), ("monkey_setup", "1.0.0", "cc")],
    [("z", "1.0.0", "1"), ("a", "9.9.9", "2"), ("m", "0.0.1", "3")],
    [("with_empty_sha", "1.0.0", "")],
])
def test_control_plane_digest_matches_agent_implementation(entries):
    assert catalog_digest(entries) == _agent_compute_version(entries)


def test_digest_is_order_independent():
    a = [("x", "1.0.0", "s1"), ("y", "2.0.0", "s2")]
    assert catalog_digest(a) == catalog_digest(list(reversed(a)))


def test_digest_changes_when_a_version_is_published():
    base = [("monkey_setup", "1.0.0", "sha-a")]
    assert catalog_digest(base) != catalog_digest(base + [("monkey_setup", "2.0.0", "sha-b")])


def test_digest_changes_when_content_changes():
    assert (
        catalog_digest([("s", "1.0.0", "before")])
        != catalog_digest([("s", "1.0.0", "after")])
    )


class TestComputeFromDatabase:
    @staticmethod
    def _add(db_session, name, version, sha, *, is_active=True):
        script = Script(
            name=name, display_name=name, category="device", script_type="python",
            version=version, nfs_path=f"/s/{name}/v{version}/{name}.py",
            content_sha256=sha, param_schema={}, default_params={},
            is_active=is_active,
        )
        db_session.add(script)
        db_session.commit()
        return script

    def test_publishing_a_version_changes_the_catalog_version(self, db_session):
        before = compute_script_catalog_version(db_session)
        self._add(db_session, "wifi_probe", "1.0.0", "sha-1")
        after = compute_script_catalog_version(db_session)
        assert before != after

    def test_inactive_scripts_are_excluded(self, db_session):
        before = compute_script_catalog_version(db_session)
        self._add(db_session, "retired_thing", "1.0.0", "sha-x", is_active=False)
        assert compute_script_catalog_version(db_session) == before

    def test_legacy_aee_scripts_are_excluded_like_on_the_agent(self, db_session):
        """Agent 侧 `_load_from_server` 会跳过它们，控制面必须跳过同一批。"""
        legacy = next(iter(LEGACY_AEE_SCRIPT_NAMES), None)
        if legacy is None:
            pytest.skip("no legacy AEE script names declared")
        before = compute_script_catalog_version(db_session)
        self._add(db_session, legacy, "1.0.0", "sha-legacy")
        assert compute_script_catalog_version(db_session) == before

    def test_deactivating_a_script_changes_the_catalog_version(self, db_session):
        script = self._add(db_session, "temp_thing", "1.0.0", "sha-t")
        with_it = compute_script_catalog_version(db_session)
        script.is_active = False
        db_session.commit()
        assert compute_script_catalog_version(db_session) != with_it
