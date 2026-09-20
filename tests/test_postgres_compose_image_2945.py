"""#2945：compose PG 镜像默认必须对齐生产大版本，且版本可配。

背景：模板曾硬编码 postgres:15-alpine，而生产是 PG17——本地测不出
rolconfig / pg_db_role_setting 等大版本差异（#2849 顺带项）。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 生产库大版本权威钉——改生产前先改这里，守卫会逼 compose 默认跟上来。
PRODUCTION_PG_MAJOR = 17

COMPOSE_FILES = (
    "docker-compose.yml",
    "deploy/postgres/docker-compose.yml",
)

_DEFAULT_RE = re.compile(
    r"image:\s*\$\{POSTGRES_IMAGE:-postgres:(?P<major>\d+)(?:-[a-z0-9]+)?\}"
)


def _compose_text(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def test_compose_postgres_image_is_env_overridable_with_prod_default() -> None:
    for rel in COMPOSE_FILES:
        text = _compose_text(rel)
        assert "image: postgres:15" not in text, f"{rel} 仍硬编码 15（#2945）"
        assert "POSTGRES_IMAGE" in text, f"{rel} 未暴露 POSTGRES_IMAGE 覆盖口"
        m = _DEFAULT_RE.search(text)
        assert m, f"{rel} 默认镜像不是 ${{POSTGRES_IMAGE:-postgres:<major>…}} 形态"
        assert int(m.group("major")) == PRODUCTION_PG_MAJOR, (
            f"{rel} 默认 major={m.group('major')} ≠ 生产 {PRODUCTION_PG_MAJOR}"
        )


def test_readme_documents_production_major() -> None:
    readme = (REPO / "deploy/postgres/README.md").read_text(encoding="utf-8")
    assert f"**{PRODUCTION_PG_MAJOR}**" in readme or f"| **{PRODUCTION_PG_MAJOR}** |" in readme
    assert "POSTGRES_IMAGE" in readme
    assert "#2945" in readme


def test_env_example_ships_matching_default() -> None:
    example = (REPO / "deploy/postgres/.env.example").read_text(encoding="utf-8")
    assert f"POSTGRES_IMAGE=postgres:{PRODUCTION_PG_MAJOR}-alpine" in example
