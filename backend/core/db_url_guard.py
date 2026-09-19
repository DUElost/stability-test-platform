# -*- coding: utf-8 -*-
"""显式 TEST_DATABASE_URL 的机器护栏（#1300，R15-R01；#2632 缺口③）。

testcontainers 兜底路径天然隔离；显式 ``TEST_DATABASE_URL`` 一旦误指生产库，
conftest ``db_session`` 的全表 ``TRUNCATE ... CASCADE`` 就是生产事故。本模块在
conftest 解析显式地址时做三道闸：

1. **隔离命名**：PostgreSQL 库名必须含 ``test``（不区分大小写，如 ``stp_test``）；
2. **运行时配置比对**：与 ``DATABASE_URL``（应用运行时/生产配置）完全相同 →
   拒绝——把运行时配置复制进 TEST_DATABASE_URL 是最可能的误用姿势；
3. **控制面 loopback 拒绝**（#2632 缺口③）：本机是控制面（仓库根带生产 env 源
   ``.env.backend``）时，显式地址指向 ``localhost``/``127.0.0.1``/``::1``/unix
   socket 即指向**本机生产实例**——库名不同也拦。为什么需要这一闸：第 2 道闸在
   本机**恒空**（ambient 没有 ``DATABASE_URL``，它只在 ``.env.backend`` 里，而
   测试不得读取该文件），于是只剩库名约定，而 2026-09-17 那次 ``postgres@stp_test``
   连库尝试两道闸全过、没出事纯属该库不存在的运气。

确需指向非常规命名的库（如共享的临时验证库），设
``STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1`` 显式豁免（记 loud warning）。
"""

from __future__ import annotations

import logging
import os
from urllib.parse import parse_qsl, urlsplit

logger = logging.getLogger(__name__)

#: 「本机是控制面」的标记物：仓库根的生产 env 源文件。**只判存在，不读内容**
#: （测试不得读取/复用 ``.env.backend`` 的连接串，见 testing.md §2）。
CONTROL_PLANE_ENV_FILE = ".env.backend"

#: loopback 主机名；host 为空 = libpq 走本机 unix socket，同样是本机实例。
#: #2794：127.0.0.0/8 整段与 unix socket 路径在 :func:`_host_is_loopback` 里另判。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class UnsafeTestDatabaseUrl(ValueError):
    """TEST_DATABASE_URL 触发生产特征护栏（拒绝指向疑似非测试库）。"""


def _allow_unsafe() -> bool:
    return os.getenv("STP_ALLOW_UNSAFE_TEST_DATABASE_URL", "").strip() in (
        "1", "true", "True", "yes",
    )


def _database_name(url: str) -> str:
    """从 SQLAlchemy URL 提取库名（postgresql* 走 path；无 path 视为空）。"""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    return parsed.path.lstrip("/").split("?")[0].split("/")[0]


def _main_checkout_root(repo_root: str) -> str | None:
    """链接工作树（worktree）→ 主检出根；非 worktree 返回 None。

    worktree 的 ``.git`` 是个文件，内容为 ``gitdir: <主检出>/.git/worktrees/<名>``。
    """
    entry = os.path.join(repo_root, ".git")
    if not os.path.isfile(entry):
        return None
    try:
        with open(entry, encoding="utf-8") as handle:
            line = handle.read().strip()
    except OSError:
        return None
    if not line.startswith("gitdir:"):
        return None
    gitdir = line.split(":", 1)[1].strip()
    return os.path.abspath(os.path.join(gitdir, os.pardir, os.pardir, os.pardir))


def control_plane_env_file_present(repo_root: str | os.PathLike[str]) -> bool:
    """仓库根是否带生产 env 源文件——「本机是控制面」的标记（#2632 缺口③）。

    只判存在、不读内容：控制面与 Agent 的运行单元都在宿主上，而该文件是生产唯一
    env 源（见 ``production-diagnostics.md`` §凭据来源）。

    **worktree 里必须回溯主检出再判**：该文件是未跟踪的本地状态，链接工作树里
    没有它——只看当前 worktree 根会让这道闸恰好漏掉所有并行 worktree（2026-09-18
    实测：首版如此写，红/绿用例发现守卫没拦，测试真的去连了本机生产实例）。
    """
    root = os.fspath(repo_root)
    if os.path.isfile(os.path.join(root, CONTROL_PLANE_ENV_FILE)):
        return True
    main_root = _main_checkout_root(root)
    return bool(
        main_root and os.path.isfile(os.path.join(main_root, CONTROL_PLANE_ENV_FILE))
    )


def _authority_host(authority: str) -> str | None:
    """``[host][:port]`` → host；形态不可解析返回 None（调用方 fail closed）。"""
    if not authority:
        return ""
    if authority.startswith("["):                 # IPv6 字面量
        end = authority.find("]")
        return authority[1:end] if end != -1 else None
    if ":" in authority:
        host, _, port = authority.rpartition(":")
        if not port.isdigit():
            return None                           # 非端口形态：不猜
        return host
    return authority


def _host_candidates(url: str) -> list[str] | None:
    """枚举 DSN 在 libpq 语义下**可能连接**的全部 host；不可解析返回 None（fail closed）。

    #2794：``urlsplit(...).hostname`` 只读 authority 段，漏掉三类绕过形态——
    - **query 覆盖**：``?host=127.0.0.1`` / ``?hostaddr=127.0.0.1``（libpq 以 query 为准，
      覆盖 authority 里的主机）；
    - **multihost**：``h1:5432,127.0.0.1:5432``（逗号分隔，libpq 逐个尝试）；
    - **unix socket 路径**：``?host=/var/run/postgresql``（本机实例的另一种写法）。

    返回空列表 = 没有可用 host 信息（由调用方按「本机」处理）。
    """
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if parsed.scheme and not parsed.scheme.startswith("postgresql"):
        return []                                 # 非 PG 由 scheme 闸负责，不做 loopback 判定
    hosts: list[str] = []
    netloc = parsed.netloc.rsplit("@", 1)[-1]     # 去 userinfo（不影响 host 判定）
    if netloc:
        for part in netloc.split(","):            # multihost：逐个枚举，任一命中即算本机
            host = _authority_host(part.strip())
            if host is None:
                return None
            hosts.append(host)
    else:
        hosts.append("")                          # 空 host = libpq 走本机 socket/默认
    try:
        for key, value in parse_qsl(parsed.query, keep_blank_values=True):
            if key.lower() in ("host", "hostaddr"):
                hosts.extend(part.strip() for part in str(value).split(","))
    except ValueError:
        return None
    return hosts


def _host_is_loopback(host: str) -> bool:
    """单个 host 是否落在本机：空串（socket/默认）、unix socket 路径、loopback 名/段。"""
    value = (host or "").strip().strip("[]")
    if value == "" or value.startswith("/"):
        return True                               # 空 = 本机默认；``/…`` = unix socket 路径
    lowered = value.lower()
    if lowered == "localhost" or lowered in _LOOPBACK_HOSTS:
        return True
    if value.startswith("127.") or value.startswith("::ffff:127."):
        return True                               # 127.0.0.0/8 与 v4-mapped 形态
    return lowered in ("0.0.0.0", "::")


def _is_loopback(url: str) -> bool:
    """显式地址是否指向本机（#2794 加固：枚举全部 host，任一命中即拒）。

    解析不出 host 形态时**按本机处理**（fail closed）——该函数只在控制面闸里被调用，
    宁可多拒一个非常规 DSN（有 ``STP_ALLOW_UNSAFE_TEST_DATABASE_URL`` 出口），
    也不放一个能绕过判定、把 TRUNCATE 打到生产实例的写法进来。
    """
    hosts = _host_candidates(url)
    if hosts is None:
        return True
    if not hosts:
        return False
    return any(_host_is_loopback(host) for host in hosts)


def guard_test_database_url(
    url: str,
    *,
    runtime_database_url: str | None = None,
    on_control_plane_host: bool = False,
) -> str:
    """校验显式 TEST_DATABASE_URL；通过则原样返回，触发护栏抛
    :class:`UnsafeTestDatabaseUrl`。

    testcontainers 兜底路径**不经此函数**（容器 URL 由本模块生成，天然隔离）。
    ``on_control_plane_host`` 由调用方从 :func:`control_plane_env_file_present`
    求得（conftest 传仓库根）；其他开发机与 CI 上传 False，第 3 道闸不参与判定。
    """
    if _allow_unsafe():
        logger.warning(
            "test_db_guard_bypassed url=%s — STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1，"
            "TRUNCATE 将作用于该库，请确认不是生产库",
            url,
        )
        return url

    scheme = urlsplit(url).scheme
    if scheme and not scheme.startswith("postgresql"):
        raise UnsafeTestDatabaseUrl(
            f"TEST_DATABASE_URL must be a PostgreSQL URL (got scheme {scheme!r}); "
            "unset TEST_DATABASE_URL to use the testcontainers default"
        )

    dbname = _database_name(url)
    if "test" not in dbname.lower():
        raise UnsafeTestDatabaseUrl(
            f"TEST_DATABASE_URL database name must contain 'test' "
            f"(isolated-naming convention), got dbname={dbname!r}; "
            "unset TEST_DATABASE_URL to use the testcontainers default, or set "
            "STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1 to override"
        )

    runtime = runtime_database_url or ""
    if runtime and url == runtime:
        raise UnsafeTestDatabaseUrl(
            "TEST_DATABASE_URL is identical to DATABASE_URL (runtime config) — "
            "refusing to TRUNCATE the application database"
        )

    if on_control_plane_host and _is_loopback(url):
        raise UnsafeTestDatabaseUrl(
            "TEST_DATABASE_URL points at a loopback address while this checkout carries "
            f"the production env source ({CONTROL_PLANE_ENV_FILE}): on this host "
            "127.0.0.1/localhost is the production PostgreSQL instance. A different "
            "database name on the same instance is not isolation (#2632). Unset "
            "TEST_DATABASE_URL to use the testcontainers default, or set "
            "STP_ALLOW_UNSAFE_TEST_DATABASE_URL=1 to override"
        )
    return url
