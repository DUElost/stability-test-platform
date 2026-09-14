"""安全与会话域 Settings（ADR-0042 P2 第一优先域）。

覆盖 cookie/会话命名与策略、注册开关、CSRF 开关、CORS 白名单——这些旋钮的
**校验语义分散**在 `backend/core/security.py` 的
:func:`validate_production_auth_cookie_settings` 与 `backend/core/cors.py` 的
`get_cors_config()` 里；本模块把它们收敛为单一来源（字段值 + 派生属性），
校验函数改为消费同源取值。

口径（ADR-0042 v1.0/v1.1）：

- 只读 `os.environ`（`env_file=None`）；`.env` 来源与优先级仍由 `env_source.py` 决定；
- **字段名 = env 名小写**、默认值与迁移前 `os.getenv(...)` 逐一相同；
- **C2 凭据边界**：`JWT_SECRET_KEY` / `SSH_CREDENTIALS_FERNET_KEY` 等凭据**不进本表**
  （保持裸读）；`ENV` / `TESTING` 是跨域开关，亦不在本域（见 `is_production_like_env`）；
- 字符串语义保留：`AUTH_COOKIE_SECURE`（`"1"` 才为开）、`STP_ALLOW_REGISTER`（空=按环境）、
  `STP_CSRF_ENABLED`（`0/false/no/off` 为关）在迁移前都是**字符串比较**，此处保持
  不引入新类型/新校验（避免把「静默回落」变成「启动报错」的行为变更——是否收紧属
  独立裁决，见 ADR Revisit）。

惰性：`get_auth_session_settings()`（lru_cache）+ `reset_auth_session_settings_cache()`。
"""

from __future__ import annotations

from functools import lru_cache

from backend.core.settings.base import DomainSettings

#: CORS 默认白名单（迁移前位于 backend/core/cors.py 的 DEFAULT_CORS_*，外部零引用）
_DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000"
)
_DEFAULT_CORS_METHODS = "GET,POST,PUT,PATCH,DELETE,OPTIONS"
_DEFAULT_CORS_HEADERS = "Authorization,Content-Type,X-Agent-Secret"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


class AuthSessionSettings(DomainSettings):
    """cookie/会话/注册/CSRF/CORS 旋钮（env 名 = 字段名大写）。"""

    # ── cookie 会话（ADR-0024）──
    auth_access_cookie_name: str = "stp_access_token"
    auth_refresh_cookie_name: str = "stp_refresh_token"
    auth_cookie_path: str = "/"
    auth_cookie_secure: str = "0"       # "1" 才为开（字符串语义，迁移前一致）
    auth_cookie_samesite: str = "lax"

    # ── 注册与 CSRF 开关 ──
    stp_allow_register: str = ""        # 空=按环境；1/true/yes/on=开；0/false/no/off=关
    stp_csrf_enabled: str = "1"

    # ── CORS 白名单（CSV 原串；解析与通配符校验留在 cors.get_cors_config）──
    cors_origins: str = _DEFAULT_CORS_ORIGINS
    cors_allow_methods: str = _DEFAULT_CORS_METHODS
    cors_allow_headers: str = _DEFAULT_CORS_HEADERS

    # ── 派生语义（与迁移前的函数逐字对齐）──
    @property
    def cookie_secure_enabled(self) -> bool:
        return self.auth_cookie_secure == "1"

    @property
    def cookie_samesite_raw(self) -> str:
        """原始值小写归一（**不做合法性回落**）——生产 guard 用它校验显式非法值。"""
        return self.auth_cookie_samesite.strip().lower()

    @property
    def cookie_samesite_normalized(self) -> str:
        """取值非法时回落 `lax`（迁移前 `_get_cookie_samesite` 语义）。"""
        value = self.cookie_samesite_raw
        return value if value in {"lax", "strict", "none"} else "lax"

    @property
    def allow_register_raw(self) -> str:
        return self.stp_allow_register.strip().lower()

    @property
    def csrf_raw(self) -> str:
        """原始值小写归一——guard 用它判「显式关闭」。"""
        return self.stp_csrf_enabled.strip().lower()

    @property
    def csrf_enabled(self) -> bool:
        raw = self.csrf_raw
        if raw in _FALSY:
            return False
        if raw in _TRUTHY:
            return True
        return True  # 未设或未知值：默认开（与迁移前 guard 语义一致）


@lru_cache(maxsize=1)
def get_auth_session_settings() -> AuthSessionSettings:
    """取安全与会话域 Settings（惰性 + 缓存；不读 `.env` 文件）。"""
    return AuthSessionSettings()


def reset_auth_session_settings_cache() -> None:
    """清缓存——测试改 env 后调用（控制面不热更，见 ADR-0042 D4）。"""
    get_auth_session_settings.cache_clear()
