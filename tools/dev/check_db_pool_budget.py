#!/usr/bin/env python3
"""启动期连接预算门禁：池上限之和必须落在 PG 可用槽内（ADR-0047 D1 / #2959）。

用法（仓库根）::

    ./venv/bin/python tools/dev/check_db_pool_budget.py

不变量（ADR-0047 v1.1 裁决，2026-09-23）::

    n_instances × n_engines × (pool_size + max_overflow)
        ≤ max_connections − superuser_reserved_connections − reserved_connections
          − 非应用连接预算/运维恢复预留

出处与口径：

- 池容量取自 `backend.core.database.pool_capacity()`（**单一读数口**）——本工具
  不另算一份，避免「校验器与被校验对象各持一份算术」在下一次改默认值时漂移；
- `n_instances` = `STP_DB_POOL_INSTANCES`（默认 1，当前拓扑事实；ADR-0027 多实例推进时
  显式改这个数，不重开讨论）；
- 预留 = `STP_DB_CONNECTION_RESERVE`（默认 8，覆盖备份/迁移/人工 psql 等非应用连接与
  运维恢复入口）；
- PG 三项上限**现算**（`SHOW`），不硬编码 97；`reserved_connections` 在 PG < 17 不存在，
  按 0 处理。

fail-closed 的**例外范围**（2026-09-23 裁决）：只容忍「旧 PG 没有 `reserved_connections`
这个 GUC」（SQLSTATE `42704` / `psycopg.errors.UndefinedObject`）。连接中途断开、
权限不足、其它任何读失败**一律退出非零**——宽口径 `except Exception` 会在「读到一半掉线」
时退回默认值，那是事实上的 fail-open，门禁会在最需要它的时候失效。

`--env-file` 的语义：**ambient 进程环境优先**（生产 systemd `EnvironmentFile` 就在这一层），
文件只补缺项；除 `DATABASE_URL` 外，预算相关键（`_BUDGET_ENV_KEYS`）也一并注入，
否则手工检查可能验的是默认值而不是文件里写的值。输出里的 `config_source=` 标明本次
生效来源（`env` / `env-file` / `env+env-file` / `default`）。

退出码：预算成立 0；不成立 1（systemd `ExecStartPre` **不带减号** ⇒ 拒绝启动，
与 `check_alembic_at_head.py` 同为硬门禁）。未配置 `DATABASE_URL` 或走 SQLite（开发机）
时打印 WARN 并退出 0——开发不该被生产预算挡路，但**生产 unit 里这一行没有减号**。

为什么是「拒绝启动」而不是运行期降级：本仓对「配置误配比回退默认更危险」已有判读
（`_pool_env_int` 注释：`pool_size=0` 会让每次借连接直接抛 `QueuePool limit ... reached`）。
静默降级会把「配置非法」伪装成「负载高」，正是 #2959/R523 事故里最难分辨的那一层。
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[2]

#: PG 侧不设时按默认值兜底（与 `SHOW` 的语义一致；见 ADR-0047 背景表）
_PG_DEFAULTS = {
    "max_connections": 100,
    "superuser_reserved_connections": 3,
    "reserved_connections": 0,
}

#: 只允许「GUC 不存在」这一个 SQLSTATE 走容忍分支（`reserved_connections` 在 PG<17 才缺）
_SQLSTATE_UNDEFINED_OBJECT = "42704"

#: `--env-file` 里与本门禁**预算口径**相关的键：注入这些，手工检查才会验到文件里的值。
_BUDGET_ENV_KEYS = (
    "STP_DB_POOL_SIZE",
    "STP_DB_MAX_OVERFLOW",
    "STP_DB_POOL_TIMEOUT",
    "STP_DB_POOL_INSTANCES",
    "STP_DB_CONNECTION_RESERVE",
)


def _load_env_file(path: Path) -> dict[str, str]:
    """与 `tools/dev/check_alembic_at_head.py` 同形的最小 .env 解析（不引第三方）。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def database_url(env_file: str | None = None) -> str | None:
    """DATABASE_URL 解析：ambient env → `--env-file` → 仓库根 `.env.backend` → `.env`。"""
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url and env_file:
        url = (_load_env_file(Path(env_file).expanduser()).get("DATABASE_URL") or "").strip()
    if not url and not env_file:
        url = (_load_env_file(_REPO_ROOT / ".env.backend").get("DATABASE_URL") or "").strip()
        if not url:
            url = (_load_env_file(_REPO_ROOT / ".env").get("DATABASE_URL") or "").strip()
    if not url:
        return None
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", url, count=1)


def available_slots(settings: dict[str, int]) -> int:
    """非超级用户可用的连接槽 = max − superuser_reserved − reserved（纯函数，可测）。"""
    return (
        int(settings.get("max_connections", _PG_DEFAULTS["max_connections"]))
        - int(settings.get("superuser_reserved_connections", _PG_DEFAULTS["superuser_reserved_connections"]))
        - int(settings.get("reserved_connections", _PG_DEFAULTS["reserved_connections"]))
    )


def evaluate(
    *, capacity: dict[str, int], instances: int, reserve: int, available: int
) -> tuple[bool, str]:
    """预算判定（纯函数）：返回 (是否成立, 人类可读的一行结论)。"""
    budget = int(capacity["app_total"]) * max(1, int(instances))
    headroom = int(available) - budget
    line = (
        f"app_total={capacity['app_total']}（每引擎 {capacity['per_engine']} × "
        f"{capacity['engines']}） instances={instances} budget={budget} "
        f"available={available} reserve={reserve} headroom={headroom}"
    )
    if budget + int(reserve) > int(available):
        return False, (
            f"{line} → FAIL：预算 + 预留超过可用槽 {available}（ADR-0047 D1）。"
            f"修法：调小 STP_DB_POOL_SIZE / STP_DB_MAX_OVERFLOW，或调大 PG "
            f"max_connections（须同 PR 重算），或用 STP_DB_CONNECTION_RESERVE 显式申报"
            f"运维预留；不允许带病启动。"
        )
    return True, f"{line} → OK"


def apply_env_file_config(env_file: str | None) -> dict[str, str]:
    """按「ambient 优先」把 `--env-file` 的预算键注入进程环境，返回**实际注入**的项。

    为什么需要：`pool_capacity()` 与 `_int_env()` 都读进程环境；只注入 `DATABASE_URL`
    时，`--env-file` 里的池容量/实例数/预留对判定**不可见**——手工检查会验默认值。

    优先级与 `check_alembic_at_head.py` 的 `DATABASE_URL` 解析一致：进程环境里已有的
    值优先（生产 unit 的 `EnvironmentFile` 就在这一层），文件只补缺项。
    """
    if not env_file:
        return {}
    values = _load_env_file(Path(env_file).expanduser())
    injected: dict[str, str] = {}
    for key in _BUDGET_ENV_KEYS:
        raw = (values.get(key) or "").strip()
        if raw and not (os.getenv(key) or "").strip():
            os.environ[key] = raw
            injected[key] = raw
    return injected


def config_source(injected: dict[str, str]) -> str:
    """本次预算口径的**生效来源**（输出用，防「验的是默认值」这类误判）。"""
    from_env = [
        key
        for key in _BUDGET_ENV_KEYS
        if key not in injected and (os.getenv(key) or "").strip()
    ]
    if from_env and injected:
        return "env+env-file"
    if from_env:
        return "env"
    if injected:
        return "env-file"
    return "default"


def _read_pg_settings(url: str) -> tuple[dict[str, int], list[str]]:
    """现算 PG 上限（`SHOW` 三项）+ 容忍说明。

    fail-closed 例外范围见模块 docstring：只有「`reserved_connections` 在旧 PG 不存在」
    走容忍分支（并在 notes 里留痕），其余读失败一律向上抛、由 `main` 退出非零。
    """
    settings = dict(_PG_DEFAULTS)
    notes: list[str] = []
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
        for key in settings:
            try:
                row = conn.execute(f"SHOW {key}").fetchone()
            except psycopg.errors.UndefinedObject as exc:
                sqlstate = getattr(exc, "sqlstate", None)
                if key != "reserved_connections" or sqlstate not in (
                    _SQLSTATE_UNDEFINED_OBJECT,
                    None,
                ):
                    raise
                notes.append(
                    f"{key} 不存在（PG<17，sqlstate={_SQLSTATE_UNDEFINED_OBJECT}）→ "
                    f"按 {_PG_DEFAULTS[key]} 计"
                )
                continue
            if row is None:
                raise RuntimeError(f"SHOW {key} 未返回结果")
            settings[key] = int(row[0])
    return settings, notes


def _pool_capacity() -> dict[str, int]:
    """读池容量的**单一读数口**（`backend.core.database.pool_capacity`）。

    刻意不在模块顶层 import 应用模块：`backend.core.database` 在 import 期就要求
    `DATABASE_URL` 可解析/TESTING 之类进程环境，而本工具必须支持「无 DATABASE_URL /
    SQLite → WARN 跳过」这条开发机路径（同 `check_alembic_at_head.py` 用 importlib
    载入 schema 助手的理由）。用 `importlib.import_module` 而非函数体内 `import`：
    后者会踩 `check_inner_imports.py` 的棘轮（函数体内 import 只降不升）。
    """
    return importlib.import_module("backend.core.database").pool_capacity()


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    env_file = None
    if "--env-file" in argv:
        idx = argv.index("--env-file")
        env_file = argv[idx + 1] if idx + 1 < len(argv) else None

    # 显式指定的 env 文件读不到 ⇒ fail-closed（同「不得退回默认值」的口径）：
    # 静默退回默认会让「以为验的是这个文件」的门禁变成一次假绿。
    if env_file is not None and not Path(env_file).expanduser().is_file():
        print(f"[FAIL] --env-file 不存在或不可读：{env_file}（fail-closed，不退回默认）")
        return 1

    # 预算口径相关的 env 先注入（ambient 优先，文件补缺项）——必须在 _pool_capacity()
    # 与 _int_env() 之前，否则手工检查验的是默认值而不是 `--env-file` 里写的值。
    injected = apply_env_file_config(env_file)

    url = database_url(env_file)
    if not url:
        print("[WARN] DATABASE_URL 未配置 —— 跳过连接预算校验（开发机形态）")
        return 0
    if not url.startswith("postgresql"):
        print(f"[WARN] DATABASE_URL 非 PostgreSQL（{url.split(':', 1)[0]}）—— 跳过连接预算校验")
        return 0

    # 把解析出的 URL 交给进程环境：`backend.core.database` 在 import 时**自行**解析
    # DATABASE_URL（`--env-file` 只作用于本工具）。不导出这一步，两边就会看到不同的库
    # ——本工具会拿 `--env-file` 的目标去比对面源的池参数，静默错配（实测踩过）。
    os.environ["DATABASE_URL"] = url

    sys.path.insert(0, str(_REPO_ROOT))
    capacity = _pool_capacity()  # 延迟到确认要走 PG 再导入（见该函数 docstring）

    instances = _int_env("STP_DB_POOL_INSTANCES", 1)
    reserve = _int_env("STP_DB_CONNECTION_RESERVE", 8)

    try:
        settings, notes = _read_pg_settings(url)
    except Exception as exc:  # noqa: BLE001 — 不可判定即 fail-closed（含读一半掉线）
        print(
            f"[FAIL] 读取 PG 连接上限失败（fail-closed，不退回默认值）："
            f"{type(exc).__name__}: {exc}"
        )
        return 1
    for note in notes:
        print(f"[INFO] {note}")

    ok, line = evaluate(
        capacity=capacity,
        instances=instances,
        reserve=reserve,
        available=available_slots(settings),
    )
    print(f"[{'OK' if ok else 'FAIL'}] {line} config_source={config_source(injected)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
