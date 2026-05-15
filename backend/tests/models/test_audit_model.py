"""AuditLog schema regression tests."""

from sqlalchemy import DateTime, String

from backend.models.audit import AuditLog


class TestAuditLogModel:
    def test_resource_id_is_string_64(self):
        resource_id_type = AuditLog.__table__.c.resource_id.type
        assert isinstance(resource_id_type, String)
        assert resource_id_type.length == 64

    def test_timestamp_is_timezone_aware(self):
        timestamp_type = AuditLog.__table__.c.timestamp.type
        assert isinstance(timestamp_type, DateTime)
        assert timestamp_type.timezone is True
