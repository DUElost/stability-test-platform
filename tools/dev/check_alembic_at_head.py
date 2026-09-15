#!/usr/bin/env python3
"""Assert production DB ``alembic_version`` matches code head (#1882).

Usage (from repo root)::

    ./venv/bin/python tools/dev/check_alembic_at_head.py

Resolves ``DATABASE_URL`` from ambient env first, then repo-root
``.env.backend``, then ``.env`` (``--env-file`` 可显式指定，取代默认回落——
测试/运维用它可以避免读到本机生产 env)。When ``DATABASE_URL`` is not
configured, prints a WARN and exits 0 (dev machines without DB). When
configured, compares ``SELECT version_num FROM alembic_version`` to the single
alembic head from ``backend/alembic.ini``.

``--allow-behind``（#2062）：只拒绝「库超前于代码 / 修订未知」，库**落后**只 WARN。
手工部署路径（`tools/dev/check-deploy-source.sh`）按 runbook 是
「`git pull` → 守卫 → `alembic upgrade head`」，pulled 代码带来新迁移那一刻库必然是
落后的合法中间态——要求精确相等会把部署第一步变成必红。systemd 侧（`ExecStartPre`）
不带该参数，保持精确相等（那里硬检查位于 `upgrade head` 之后）。
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import psycopg

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_schema_revision_helpers():
    import importlib.util

    mod_path = _REPO_ROOT / "backend" / "core" / "schema_revision.py"
    spec = importlib.util.spec_from_file_location("stp_schema_revision", mod_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_schema_revision = _load_schema_revision_helpers()
code_head_revision = _schema_revision.code_head_revision
is_schema_at_head = _schema_revision.is_schema_at_head


def _load_env_file(path: Path) -> dict[str, str]:
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


def _database_url(env_file: str | None = None) -> str | None:
    url = (os.getenv("DATABASE_URL") or "").strip()
    if not url and env_file:
        env = _load_env_file(Path(env_file).expanduser())
        url = (env.get("DATABASE_URL") or "").strip()
    if not url and not env_file:
        env = _load_env_file(_REPO_ROOT / ".env.backend")
        url = (env.get("DATABASE_URL") or "").strip()
        if not url:
            env = _load_env_file(_REPO_ROOT / ".env")
            url = (env.get("DATABASE_URL") or "").strip()
    if not url:
        return None
    return re.sub(r"^postgresql\+[^:]+://", "postgresql://", url, count=1)


def known_revisions() -> set[str]:
    """代码库里全部 revision（单头仓库即 head 的祖先闭包）——判「落后 vs 超前」用。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_REPO_ROOT / "backend" / "alembic.ini"))
    # ini 里的 script_location 是相对值（相对 CWD），显式钉到绝对路径——
    # 否则从仓库根运行会报 "Path doesn't exist: alembic"。
    cfg.set_main_option("script_location", str(_REPO_ROOT / "backend" / "alembic"))
    script = ScriptDirectory.from_config(cfg)
    return {rev.revision for rev in script.walk_revisions()}


def classify_schema_state(
    db_revision: str | None, head: str, ancestry: set[str],
) -> str:
    """库相对代码 head 的位置：``at_head`` / ``behind`` / ``ahead``（#2062）。

    空 ``alembic_version``（未迁移）记 ``behind``；不在祖先闭包里的 revision
    （未知/超前）记 ``ahead``。
    """
    if db_revision == head:
        return "at_head"
    if db_revision is None:
        return "behind"
    return "behind" if db_revision in ancestry else "ahead"


def main(argv: list[str] | None = None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="校验库 schema 与代码 head 对齐（只读）")
    parser.add_argument("--env-file", default=None, help="显式 env 文件（取代默认回落）")
    parser.add_argument(
        "--allow-behind",
        action="store_true",
        help="库落后于代码 head 只 WARN（手工部署路径：pull→守卫→迁移）",
    )
    args = parser.parse_args(argv)

    url = _database_url(args.env_file)
    if not url:
        print(
            "check_alembic_at_head: WARN —— DATABASE_URL 未配置，跳过 schema 对齐检查",
            file=sys.stderr,
        )
        raise SystemExit(0)

    try:
        head = code_head_revision()
    except RuntimeError as exc:
        print(f"check_alembic_at_head: FAIL —— {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version LIMIT 1")
                row = cur.fetchone()
                db_revision = row[0] if row else None
    except Exception as exc:
        print(
            f"check_alembic_at_head: FAIL —— 无法读取 alembic_version: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    # 快路径：已对齐就不必扫 alembic 脚本目录（也就不会因 ini 缺失而改变行为）
    state = (
        "at_head" if is_schema_at_head(db_revision, head)
        else classify_schema_state(db_revision, head, known_revisions())
    )
    if state == "behind" and args.allow_behind:
        print(
            "check_alembic_at_head: WARN —— 库落后于代码 head "
            f"(alembic_version={db_revision!r}, head={head!r})；"
            "手工部署路径下属正常中间态，迁移完成后本检查应转 OK",
            file=sys.stderr,
        )
        return
    if state != "at_head":
        print(
            "check_alembic_at_head: FAIL —— "
            f"alembic_version={db_revision!r} != code head {head!r}（{state}）",
            file=sys.stderr,
        )
        print(
            "  先执行：cd backend && python -m alembic upgrade head",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"check_alembic_at_head: OK —— alembic_version={db_revision!r} == head {head!r}")


if __name__ == "__main__":
    main()
