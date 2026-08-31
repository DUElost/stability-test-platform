"""迁移后 schema ↔ ORM 模型一致性守卫（#644 P0 复盘产物）。

背景：uq_project_model_active 唯一索引在生产丢失（a9b8c7d6e5f4 删
match_type 列时 PG 静默连带删索引、未重建），而测试用
Base.metadata.create_all()（索引在），pr-migrate-empty-db 只验「迁移能
跑通」不验「结果与模型一致」——结构上无法发现 model↔migration 漂移。

本脚本用 alembic.autogenerate.compare_metadata 对比迁移结果与 ORM 模型，
**基线白名单**模式：既有漂移（FK ondelete 参数、索引命名、partial index
比较 bug 等 24 项历史噪音）固化为基线，断言 diff ⊆ 基线。全空断言在当前
代码库不成立（历史噪音需另案收敛）；新增漂移（基线外项）必拦。

用法：
    python -m backend.scripts.check_schema_sync [--rebaseline]
  --rebaseline 把当前 diff keys 写回基线文件（人工确认后使用）

DATABASE_URL 解析：环境变量最优先，否则 .env.backend（同 alembic env）。
CI（pr-migrate-empty-db）里 DATABASE_URL 指向空库 postgres 服务。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from backend.core.database import Base
from backend.core.env_source import resolve_database_url

import backend.models  # noqa: F401 确保全部模型注册到 metadata

_BASELINE_FILE = Path(__file__).parent / "schema_sync_baseline.json"


def _diff_key(d: tuple | list) -> str:
    """把 alembic diff 项规范化为稳定 key。

    形态繁杂（索引/表/FK/列级变更）；部分 alembic 版本把 modify 类变更包成
    ``[('modify_type', ...)]`` 单元素 list（``d[0]`` 是整元组），须先解包，
    否则回退 key 会含对象 repr 的内存地址——每次运行都变，永不匹配基线。
    """
    while isinstance(d, (list, tuple)) and d and not isinstance(d[0], str):
        d = d[0]
    kind = d[0]
    try:
        if kind in ("add_index", "remove_index"):
            return f"{kind}|{d[1].table.name}|{d[1].name}"
        if kind in ("add_table", "remove_table"):
            return f"{kind}|{d[1].name}"
        if kind in ("add_fk", "remove_fk"):
            fk = d[1]
            tbl = getattr(fk, "table", None)
            tname = tbl.name if tbl is not None else "?"
            cols = ",".join(str(getattr(c, "name", c)) for c in fk.columns)
            return f"{kind}|{tname}|{cols}"
        if kind in (
            "modify_type", "modify_nullable", "modify_default",
            "add_column", "remove_column",
        ):
            return f"{kind}|{d[2]}|{d[3]}"
    except Exception:
        pass
    return f"{kind}|{str(d)[:120]}"


def _run_upgrade(db_url: str) -> None:
    backend_dir = Path(__file__).resolve().parents[1]  # backend/
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")


def _collect_diffs(db_url: str) -> list[tuple]:
    url = re.sub(r"\+asyncpg", "+psycopg", db_url)
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            mc = MigrationContext.configure(conn)
            return compare_metadata(mc, Base.metadata)
    finally:
        engine.dispose()


def _resolve_url() -> str:
    import os

    ambient = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if ambient:
        return ambient
    resolved, _source = resolve_database_url()
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebaseline", action="store_true",
                        help="把当前 diff keys 写回基线文件")
    args = parser.parse_args()

    db_url = _resolve_url()
    _run_upgrade(db_url)
    diffs = _collect_diffs(db_url)
    keys = [_diff_key(d) for d in diffs]

    baseline = set()
    if _BASELINE_FILE.exists():
        baseline = set(json.loads(_BASELINE_FILE.read_text(encoding="utf-8")))
    new_keys = [k for k in keys if k not in baseline]

    if args.rebaseline:
        # 覆盖语义（非并集）：基线 = 当前空库 diff keys 全集——收敛掉
        # 的噪音项随之消失（并集会让已修复项永久留在基线里）
        merged = sorted(keys)
        _BASELINE_FILE.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"rebaseline: {len(merged)} keys 已写回 {_BASELINE_FILE}")
        return 0

    print(f"compare_metadata diff: {len(diffs)} 项（基线 {len(baseline)}，新增 {len(new_keys)}）")
    for k in keys:
        mark = " " if k in baseline else "!! NEW"
        print(f"  [{mark}] {k}")
    if new_keys:
        print("ERROR: 迁移结果与 ORM 模型出现基线外漂移。")
        print("      修迁移/模型使其一致；确系预期则人工确认后 --rebaseline。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
