"""#942：种子迁移治理模板的带数据行为测试（真实 Postgres）。

覆盖裁决 A 的三分支：无引用放行 / 有引用失败（含指引文案）/ 批量停用形态。
表结构用最小建表——模板只依赖 ``plan_step.script_name/script_version`` 与
``script`` 表的存在，不依赖完整 schema。
"""

from __future__ import annotations

from pathlib import Path

import re

import pytest
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

from backend.services.script_seed_governance import (
    count_plan_step_references,
    raise_if_any_version_referenced,
    raise_if_version_referenced,
)


def _normalize(url: str) -> str:
    return url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="module")
def pg_engine():
    with PostgresContainer("postgres:16") as postgres:
        engine = create_engine(_normalize(postgres.get_connection_url()))
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE TABLE plan_step ("
                    " id serial PRIMARY KEY,"
                    " plan_id integer,"
                    " script_name varchar(64) NOT NULL,"
                    " script_version varchar(32) NOT NULL)"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE script ("
                    " id serial PRIMARY KEY,"
                    " name varchar(64) NOT NULL,"
                    " version varchar(32) NOT NULL,"
                    " is_active boolean NOT NULL DEFAULT true)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO script (name, version, is_active) VALUES "
                    "('flash_firmware', '1.3.1', true), "
                    "('flash_firmware', '1.3.0', true)"
                )
            )
        yield engine
        engine.dispose()


def test_unreferenced_version_passes(pg_engine):
    """无引用 → 模板放行（种子覆写/停用可执行）。"""
    with pg_engine.connect() as conn:
        assert (
            count_plan_step_references(
                conn, script_name="flash_firmware", script_version="1.3.0"
            )
            == 0
        )
        raise_if_version_referenced(
            conn, script_name="flash_firmware", script_version="1.3.0"
        )


def test_referenced_version_aborts_with_guidance(pg_engine):
    """有引用 → RuntimeError 且指引可读（迁移失败，重指后重跑）。"""
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plan_step (plan_id, script_name, script_version) "
                "VALUES (1, 'flash_firmware', '1.3.1')"
            )
        )
    with pytest.raises(RuntimeError, match="仍被 1 个 plan_step 引用"):
        with pg_engine.connect() as conn:
            raise_if_version_referenced(
                conn, script_name="flash_firmware", script_version="1.3.1"
            )


def test_batch_deactivate_aborts_listing_all_blocked(pg_engine):
    """批量停用形态：任一被引用即失败，错误列出全部被堵版本。"""
    with pg_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO plan_step (plan_id, script_name, script_version) "
                "VALUES (2, 'flash_firmware', '1.3.0')"
            )
        )
    with pytest.raises(RuntimeError) as exc_info:
        with pg_engine.connect() as conn:
            raise_if_any_version_referenced(
                conn,
                script_name="flash_firmware",
                versions=["1.3.0", "1.3.1"],
            )
    # 两个被堵版本都在指引里
    assert "1.3.0 ×1" in str(exc_info.value)
    assert "1.3.1 ×1" in str(exc_info.value)


# ── #2055：seed 迁移文件本身也要受治理（此前只测了服务层模块）──────────────

SEED_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "backend" / "alembic" / "versions"


def _seed_files_with_deactivation() -> list[Path]:
    """含 `is_active = false`（即停用既有版本）的迁移文件。"""
    return sorted(
        p for p in SEED_VERSIONS_DIR.glob("*.py")
        if "is_active = false" in p.read_text(encoding="utf-8")
    )


#: 存量豁免（#2055）：2026-09-12 及之前新增、且已在生产应用的 seed——#942 治理模板
#: 自 2026-09-13 起才随新 seed 生效（对照：`a3b2c1d0e9f8` 起都带检查）。追溯改造这些
#: 已应用迁移会改变「全新安装」的行为（引用存在时直接中止部署），故本轮只做**前向**守卫。
#: 终态出口：随零引用版本退役（#735）自然收敛；新增文件一律不得进入本表。
_LEGACY_SEEDS_WITHOUT_REF_CHECK = {
    "a7b8c9d0e1f2", "b7c8d9e0f1a2", "b8c9d0e1f2a3", "c0d1e2f3a4b5", "c9d0e1f2a3b4",
    "d3e4f5a6b7c8", "e1f2a3b4c5d6", "e7f8a9b0c1d2", "f0a1b2c3d4e5", "g1h2i3j4k5l6",
    "g5a6b7c8d9e0", "h2i3j4k5l6m7", "h8i9j0k1l2m3", "i5j6k7l8m9n0", "j0k1l2m3n4o5",
    "k1l2m3n4o5p6", "o9p8q7r6s5t4", "p8q7r6s5t4u3", "p9q8r7s6t5u4", "q3r4s5t6u7v8",
    "q7r6s5t4u3v2", "r6s5t4u3v2w1", "s2t3u4v5w6x7", "s5t4u3v2w1x0", "t2u3v4w5x6y7",
    "u3v4w5x6y7z8", "u6v5w4x3y2z1", "u7v8w9x0y1z2", "v5w4x3y2z1a0", "w4x3y2z1a0b9",
}


def _revision_of(path: Path) -> str:
    m = re.search(
        r'^revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)',
        path.read_text(encoding="utf-8"),
        re.M,
    )
    return m.group(1) if m else path.stem[:12]


def test_new_seed_migrations_deactivating_versions_check_references():
    """#2055：**新增** seed 凡停用既有版本，必须先做 plan_step 引用检查（#942 裁决 A）。

    服务层的治理单测覆盖不到迁移文件本身——实测 2026-09-12 前有 30 个 legacy seed 缺这步
    （见 `_LEGACY_SEEDS_WITHOUT_REF_CHECK` 与 Agent Note 的 Revisit）。本守卫只对新文件生效。
    """
    offenders: list[str] = []
    for path in _seed_files_with_deactivation():
        if _revision_of(path) in _LEGACY_SEEDS_WITHOUT_REF_CHECK:
            continue
        text = path.read_text(encoding="utf-8")
        defines = "def _raise_if_any_version_referenced(" in text
        calls = "_raise_if_any_version_referenced(" in text.replace(
            "def _raise_if_any_version_referenced(", ""
        )
        if not (defines and calls):
            offenders.append(path.name)
    assert not offenders, (
        "以下**新增**迁移会停用版本但没有引用检查（#942/#2055）："
        f"{offenders}——停用仍被 plan_step 引用的版本应在迁移期失败并给出重指指引"
    )


def test_legacy_allowlist_has_no_stale_entries():
    """豁免表不得留下「其实已带检查」或已删除的 revision（防豁免面悄悄扩大）。"""
    present = {_revision_of(p) for p in _seed_files_with_deactivation()}
    stale = sorted(_LEGACY_SEEDS_WITHOUT_REF_CHECK - present)
    assert not stale, f"豁免表存在失效条目（请移除）：{stale}"


def test_seed_migrations_do_not_delete_script_rows_on_downgrade():
    """#2055：downgrade 用 DELETE 会删掉并非本迁移创建的行——应翻转 is_active。"""
    offenders = [
        p.name
        for p in sorted(SEED_VERSIONS_DIR.glob("*.py"))
        if "DELETE FROM script " in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"以下迁移的 downgrade 用 DELETE 而非 is_active 翻转：{offenders}"
