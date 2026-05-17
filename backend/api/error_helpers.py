from __future__ import annotations

from fastapi import HTTPException


def raise_api_http_error(status_code: int, code: str, message: str) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": code,
            "message": message,
        },
    )
