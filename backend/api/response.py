"""Unified API response helpers.

All new Phase 3+ routes return ApiResponse[T] for consistent error handling.
"""

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str


class ApiResponse(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[ErrorDetail] = None


def ok(data: T) -> ApiResponse[T]:
    return ApiResponse(data=data, error=None)


def err(code: str, message: str) -> ApiResponse:
    return ApiResponse(data=None, error=ErrorDetail(code=code, message=message))
