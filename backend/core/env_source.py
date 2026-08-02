"""生产 env 的唯一事实源解析。

背景（2026-08-01）：仓库里有两份 env 文件，长期不一致 ——

- 仓库根 ``.env.backend``：systemd 的 ``EnvironmentFile``，**生产唯一事实源**
- ``backend/.env``：本地开发覆盖，但 ``main.py`` 会无条件 ``load_dotenv`` 它

两者的 ``DATABASE_URL`` / ``JWT_SECRET_KEY`` / ``SSH_CREDENTIALS_FERNET_KEY``
/ ``AGENT_SECRET`` / ``REDIS_URL`` 全都不同。systemd 启动时 ambient 变量优先，
侥幸没出事；但任何手工启动或 CLI 都会静默落到另一套配置上 —— 连不通的库、
解不开的 SSH 凭据、对不上的会话。

更危险的是 ``alembic/env.py`` 与 ``core/database.py`` 的**兜底默认**都写死成
``postgresql+psycopg://stp:password@localhost:5432/stp`` —— 直接点名**生产库**，
只是密码是占位符。今天靠密码错拦住，但只要那个密码对上（或服务端放开 trust
认证），干净 shell 里一条 ``alembic upgrade`` 就会静默改写生产库。

所以这里**不提供任何兜底默认**：解析不到就报错。注意「断言库名必须是 stp」
这类护栏对上面那个场景是无效的 —— 危险路径的默认值本来就叫 stp。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ENV_FILE = REPO_ROOT / ".env.backend"


def _read_key(env_file: Path, key: str) -> Optional[str]:
    """Minimal ``KEY=value`` reader — no dotenv dependency, no side effects."""
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return None


def redact_url(url: str) -> str:
    """Strip credentials so a URL can be logged."""
    return re.sub(r"(://)[^:/@]+:[^@]*@", r"\1<redacted>@", url)


def resolve_database_url(
    *,
    env_file: Optional[Path] = None,
    environ: Optional[dict] = None,
) -> Tuple[str, str]:
    """Return ``(url, source)`` — ambient ``DATABASE_URL`` first, then env file.

    ``backend/.env`` is deliberately not consulted: it is a developer override
    file, and letting migrations pick it up is how DDL lands on the wrong
    database. Raises ``RuntimeError`` when neither source provides a URL —
    guessing is what made this dangerous in the first place.
    """
    env = os.environ if environ is None else environ
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        return url, "environment"

    target = PRODUCTION_ENV_FILE if env_file is None else env_file
    url = (_read_key(target, "DATABASE_URL") or "").strip()
    if url:
        return url, str(target)

    raise RuntimeError(
        f"DATABASE_URL is not set and {target} has no DATABASE_URL.\n"
        "Refusing to guess a migration/runtime target — set it explicitly:\n"
        "  DATABASE_URL='postgresql+psycopg://user:pass@host:5432/db' "
        "alembic upgrade head"
    )
