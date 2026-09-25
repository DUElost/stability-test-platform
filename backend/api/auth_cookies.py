"""认证 Cookie 写入（#3297 自 core/security.py 拆出）。

core 是最底层，不得依赖 web 框架（C4 基线行 `core.security → starlette` 的出口）；
`set/clear_auth_cookies` 是对 `Response` 的操作，属 HTTP 面，归 api 层。
Cookie 语义（名称/path/secure/SameSite）仍全部取自 `core.settings.security` 的
单一来源（ADR-0042），行为逐字不变；生产类环境的启动 fail-fast 仍由
`backend.core.security.validate_production_auth_cookie_settings` 负责（那是纯函数，
留在 core）。

调用方应经 api 层模块（如 `api/routes/auth.py`）import 本模块——services/scheduler
不得上引 api（C1）。
"""
from starlette.responses import Response

from backend.core.security import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    REFRESH_TOKEN_EXPIRE_DAYS,
    is_auth_cookie_secure,
)
from backend.core.settings.security import get_auth_session_settings


def _cookie_samesite() -> str:
    return get_auth_session_settings().cookie_samesite_normalized


def set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        get_auth_session_settings().auth_access_cookie_name,
        access_token,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        httponly=True,
        secure=is_auth_cookie_secure(),
        samesite=_cookie_samesite(),
        path=get_auth_session_settings().auth_cookie_path,
    )
    response.set_cookie(
        get_auth_session_settings().auth_refresh_cookie_name,
        refresh_token,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=is_auth_cookie_secure(),
        samesite=_cookie_samesite(),
        path=get_auth_session_settings().auth_cookie_path,
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(
        get_auth_session_settings().auth_access_cookie_name,
        path=get_auth_session_settings().auth_cookie_path,
        secure=is_auth_cookie_secure(),
        samesite=_cookie_samesite(),
        httponly=True,
    )
    response.delete_cookie(
        get_auth_session_settings().auth_refresh_cookie_name,
        path=get_auth_session_settings().auth_cookie_path,
        secure=is_auth_cookie_secure(),
        samesite=_cookie_samesite(),
        httponly=True,
    )
