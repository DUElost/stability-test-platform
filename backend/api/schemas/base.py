from datetime import datetime
from typing import Any, List

from pydantic import BaseModel

try:
    from pydantic import ConfigDict

    _HAS_CONFIG_DICT = True
except Exception:
    ConfigDict = None
    _HAS_CONFIG_DICT = False


def _isoformat_utc(v: datetime) -> str:
    return v.isoformat() + "Z" if v.tzinfo is None else v.isoformat()


class ORMBaseModel(BaseModel):
    if _HAS_CONFIG_DICT:
        model_config = ConfigDict(from_attributes=True, json_encoders={datetime: _isoformat_utc})
    else:
        class Config:
            orm_mode = True
            json_encoders = {datetime: _isoformat_utc}


class PaginatedResponse(BaseModel):
    """Generic paginated response wrapper."""
    items: List[Any]
    total: int
    skip: int
    limit: int
