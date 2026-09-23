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


def _read_pg_settings(url: str) -> dict[str, int]:
    """现算 PG 上限：`SHOW` 三项；`reserved_connections` 在 PG<17 不存在按 0。"""
    settings = dict(_PG_DEFAULTS)
    with psycopg.connect(url, autocommit=True, connect_timeout=5) as conn:
        for key in settings:
            try:
                row = conn.execute(f"SHOW {key}").fetchone()
            except Exception:  # noqa: BLE001 — 未知 GUC（PG<17 无 reserved_connections）
                continue
            if row is not None:
                settings[key] = int(row[0])
    return settings


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
        settings = _read_pg_settings(url)
    except Exception as exc:  # noqa: BLE001 — 连不上就是不可判定，按 fail-closed 处理
        print(f"[FAIL] 读取 PG 连接上限失败：{type(exc).__name__}: {exc}")
        return 1

    ok, line = evaluate(
        capacity=capacity,
        instances=instances,
        reserve=reserve,
        available=available_slots(settings),
    )
    print(f"[{'OK' if ok else 'FAIL'}] {line}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
