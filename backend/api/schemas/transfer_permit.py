"""传输并发许可 API schema（ADR-0064 D3 / B10 U3）。"""

from __future__ import annotations

from pydantic import Field

from backend.api.schemas.base import ORMBaseModel


class TransferPermitAcquireIn(ORMBaseModel):
    host_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=128)
    pool: str = Field(default="firmware_nfs", min_length=1, max_length=64)
    ttl_seconds: int | None = Field(default=None, ge=5, le=3600)


class TransferPermitRenewIn(ORMBaseModel):
    host_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=128)
    pool: str = Field(default="firmware_nfs", min_length=1, max_length=64)
    ttl_seconds: int | None = Field(default=None, ge=5, le=3600)


class TransferPermitReleaseIn(ORMBaseModel):
    host_id: str = Field(min_length=1, max_length=128)
    lease_token: str = Field(min_length=1, max_length=128)
    pool: str = Field(default="firmware_nfs", min_length=1, max_length=64)


class TransferPermitAcquireOut(ORMBaseModel):
    acquired: bool
    pool: str
    host_id: str
    lease_token: str
    active_count: int
    max_hosts: int
    ttl_seconds: int


class TransferPermitRenewOut(ORMBaseModel):
    renewed: bool
    pool: str
    host_id: str
    lease_token: str
    ttl_seconds: int


class TransferPermitReleaseOut(ORMBaseModel):
    released: bool
    pool: str
    host_id: str
    lease_token: str
