from datetime import datetime
from typing import Any, List

from pydantic import BaseModel, ConfigDict, field_serializer


def _isoformat_utc(v: datetime) -> str:
    return v.isoformat() + "Z" if v.tzinfo is None else v.isoformat()


class ORMBaseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialize_datetimes(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return _isoformat_utc(value)
        return value


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: List[Any]
    total: int
    skip: int
    limit: int
