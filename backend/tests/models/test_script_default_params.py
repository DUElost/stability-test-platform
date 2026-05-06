"""ADR-0020 — Script.default_params tests."""

import pytest
from sqlalchemy.exc import IntegrityError

from backend.models.script import Script


class TestScriptDefaultParams:
    def test_default_params_non_null(self, db_session):
        script = Script(
            name="test_script",
            script_type="python",
            version="1.0.0",
            nfs_path="/scripts/test/1.0.0/main.py",
            content_sha256="abc123",
        )
        db_session.add(script)
        db_session.commit()
        # default_params should default to empty dict via server_default
        assert script.default_params == {}

    def test_default_params_stored(self, db_session):
        script = Script(
            name="test_script2",
            script_type="python",
            version="1.0.0",
            nfs_path="/scripts/test2/1.0.0/main.py",
            content_sha256="abc456",
            default_params={"timeout": 30, "retry": 3},
        )
        db_session.add(script)
        db_session.commit()
        assert script.default_params == {"timeout": 30, "retry": 3}

    def test_default_params_json_form(self, db_session):
        script = Script(
            name="test_json",
            script_type="python",
            version="1.0.0",
            nfs_path="/scripts/test_json/1.0.0/main.py",
            content_sha256="json123",
            default_params={"nested": {"key": "value"}, "list": [1, 2, 3]},
        )
        db_session.add(script)
        db_session.commit()
        # Re-fetch to verify JSON round-trip
        fetched = db_session.get(Script, script.id)
        assert fetched.default_params == {"nested": {"key": "value"}, "list": [1, 2, 3]}
