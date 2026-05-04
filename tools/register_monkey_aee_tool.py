# -*- coding: utf-8 -*-
"""
手动注册 MONKEY_AEE 工具到 PostgreSQL 数据库。

用法:
    set DATABASE_URL=postgresql+psycopg://user:pass@host:5432/dbname
    python scripts/register_monkey_aee_tool.py
"""

import os
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _normalize_database_url() -> str:
    raw_url = os.getenv("DATABASE_URL", "").strip()
    if not raw_url:
        raise RuntimeError("DATABASE_URL 未设置，请指向 PostgreSQL。")

    if raw_url.startswith("sqlite:"):
        raise RuntimeError(f"检测到 sqlite 连接串，不允许使用: {raw_url}")

    if raw_url.startswith("postgresql://"):
        # 统一到 psycopg3 驱动，避免默认尝试 psycopg2
        normalized = raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
        os.environ["DATABASE_URL"] = normalized
        return normalized

    if raw_url.startswith("postgresql+psycopg://"):
        return raw_url

    raise RuntimeError(
        f"不支持的 DATABASE_URL 协议: {raw_url}。仅支持 postgresql+psycopg://"
    )


def main() -> None:
    db_url = _normalize_database_url()
    try:
        from backend.core.database import SessionLocal
        from backend.core.tool_bootstrap import ensure_monkey_aee_tool
    except ModuleNotFoundError as exc:
        if "psycopg" in str(exc):
            raise RuntimeError(
                "缺少 PostgreSQL 驱动 psycopg。请先安装依赖（pip install -r backend/requirements.txt）。"
            ) from exc
        raise

    with SessionLocal() as db:
        tool, created = ensure_monkey_aee_tool(db)
        action = "created" if created else "updated"
        print(
            f"MONKEY_AEE tool {action}: "
            f"id={tool.id}, name={tool.name}, "
            f"script_path={tool.script_path}, script_class={tool.script_class}, "
            f"db={db_url}"
        )


if __name__ == "__main__":
    main()
