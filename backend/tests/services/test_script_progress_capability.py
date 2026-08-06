"""#136/#171：stall_seconds 门禁的 PROGRESS 能力校验。"""

from backend.models.script import Script
from backend.services.script_progress_capability import script_supports_progress


def _ensure_script(db, name: str, version: str, capabilities=None):
    row = db.query(Script).filter(
        Script.name == name, Script.version == version,
    ).first()
    if row is None:
        row = Script(
            name=name,
            script_type="python",
            version=version,
            nfs_path=f"/nfs/scripts/{name}/{version}",
            content_sha256="2" * 64,
            capabilities=capabilities or [],
            is_active=True,
            default_params={},
            param_schema={},
        )
        db.add(row)
    else:
        row.capabilities = capabilities or []
        row.is_active = True
    db.commit()
    return row


class TestScriptProgressCapability:
    def test_known_capable_versions(self, db_session):
        for version in ("2.3.1", "2.3.2", "2.3.3", "2.3.4", "2.3.5"):
            _ensure_script(db_session, "monkey_setup", version, ["progress_stamps"])
        _ensure_script(db_session, "flash_firmware", "1.1.0", ["progress_stamps"])

        assert script_supports_progress(db_session, "monkey_setup", "2.3.1")
        assert script_supports_progress(db_session, "monkey_setup", "v2.3.2")
        assert script_supports_progress(db_session, "monkey_setup", "2.3.3")
        assert script_supports_progress(db_session, "monkey_setup", "v2.3.4")
        assert script_supports_progress(db_session, "monkey_setup", "2.3.5")
        assert script_supports_progress(db_session, "flash_firmware", "1.1.0")

    def test_legacy_and_unknown_versions_are_not_capable(self, db_session):
        for version in ("2.2.0", "2.3.0", "2.4.0"):
            _ensure_script(db_session, "monkey_setup", version)
        _ensure_script(db_session, "unknown_script", "1.0.0")

        assert not script_supports_progress(db_session, "monkey_setup", "2.2.0")
        assert not script_supports_progress(db_session, "monkey_setup", "v2.3.0")
        assert not script_supports_progress(db_session, "monkey_setup", "2.4.0")
        assert not script_supports_progress(db_session, "unknown_script", "v1.0.0")

    def test_version_without_v_prefix_matches(self, db_session):
        """#136 格式回归：plan_step 存无 v（2.3.4），脚本目录带 v——两种都匹配。"""
        _ensure_script(db_session, "monkey_setup", "2.3.4", ["progress_stamps"])
        assert script_supports_progress(db_session, "monkey_setup", "2.3.4")
        assert script_supports_progress(db_session, "monkey_setup", "v2.3.4")

    def test_inactive_rows_are_not_capable(self, db_session):
        _ensure_script(db_session, "monkey_setup", "2.3.4", ["progress_stamps"])
        row = db_session.query(Script).filter(
            Script.name == "monkey_setup", Script.version == "2.3.4",
        ).one()
        row.is_active = False
        db_session.commit()

        assert not script_supports_progress(db_session, "monkey_setup", "2.3.4")

    def test_unknown_script_version_returns_false_without_rows(self, db_session):
        assert not script_supports_progress(db_session, "monkey_setup", "2.3.4")
