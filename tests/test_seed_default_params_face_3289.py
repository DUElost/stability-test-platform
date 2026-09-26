"""新站 seed 参数面基线棘轮（#3289，ADR-0051 v1.4 遗留项②）。

判据（issue 验收③）：「新站参数面 ⊇/≡ 生产参数面」不变式以**登记 + 棘轮**收口——

1. **seed_face 棘轮**：alembic head 后 ``script.default_params`` 的非空键集必须等于
   ``tests/fixtures/seed_default_params_face_3289.json`` 的 ``seed_face``。新站
   bootstrap 的参数面由迁移族唯一决定（scan 新建行恒 ``{}`` 且从不改写
   ``default_params``）——任何 seed 迁移新增/删除参数键都必须**显式**更新基线，
   否则多站点（ADR-0041）新站的执行语义漂移不可归因；
2. **登记例外诚实**：``registered_exceptions`` 里每个「生产有、seed 无」的键，
   在 seed 面上必须确实缺席——例外被 seed 侧悄悄补上而登记未清，登记即失真。

⊇ 方向（生产面 ⊆ seed 面 ∪ 登记例外）需要生产参数面的受控导出物做参照，
修法路径（a 回灌 Git 侧 seed / b 导出物 + SOP）在 issue 里明确「核对后定夺」，
裁决落地前以 2026-09-26 全量核对（活跃集 107=107 零差集、缺键仅登记例外一项、
零值漂移）为人工核对基线，不在 CI 里假设。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
BASELINE = (
    REPO_ROOT / "tests" / "fixtures" / "seed_default_params_face_3289.json"
)


def _head_revision() -> str:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def _normalize_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)


def _seed_face() -> dict[str, list[str]]:
    """新站 seed 参数面（name@version → 非空 sorted keys）。

    口径 = alembic head 的 ``script`` 行 ∩ ``is_active`` ∩ 非 manifest-retired——
    与「隔离空库 alembic head + scan」的 active 面等价：scan 对 ``default_params``
    零写入（新建行恒 ``{}``），对 manifest ``retired:true`` 的既有行做显式退役
    （等价于本函数的 retired 过滤，见 ``sync_scripts_from_manifest``）。
    ``tool_manifest.json`` 在仓内，故该面 CI 完全可复现、不依赖站点包源。
    """
    with PostgresContainer("postgres:16") as postgres:
        env = os.environ.copy()
        env["DATABASE_URL"] = _normalize_database_url(postgres.get_connection_url())
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(BACKEND_DIR / "alembic.ini"),
             "upgrade", "head"],
            cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, f"upgrade head 失败:\n{result.stderr}"
        engine = create_engine(env["DATABASE_URL"])
        with engine.connect() as conn:
            rows = conn.execute(
                text("select name, version, is_active, default_params from script")
            ).fetchall()
    manifest = json.loads((REPO_ROOT / "tool_manifest.json").read_text(encoding="utf-8"))
    retired = {
        (name, str(entry["version"]))
        for name, tool in manifest["tools"].items()
        for entry in tool["versions"]
        if tool.get("kind") == "script" and entry.get("retired")
    }
    face: dict[str, list[str]] = {}
    for name, version, is_active, dp in rows:
        if not is_active or (name, str(version)) in retired:
            continue
        keys = sorted((dp or {}).keys())
        if keys:
            face[f"{name}@{version}"] = keys
    return face


def test_seed_face_matches_registered_baseline():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    face = _seed_face()
    assert face == baseline["seed_face"], (
        "seed 参数面漂移（#3289 棘轮）：\n"
        f"  多出（新 seed 了键）：{ {k: v for k, v in face.items() if baseline['seed_face'].get(k) != v} }\n"
        f"  缺失/变形：{ {k: v for k, v in baseline['seed_face'].items() if face.get(k) != v} }\n"
        "有意的 seed 参数变更必须同步更新 tests/fixtures/seed_default_params_face_3289.json"
        "（并在 #3289 注明——这会改变多站点新站的执行语义）"
    )


def test_registered_exceptions_are_absent_from_seed_face():
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    face = _seed_face()
    for key, spec in baseline["registered_exceptions"].items():
        seeded_keys = face.get(key, [])
        leaked = sorted(set(spec["missing_vs_production"]) & set(seeded_keys))
        assert not leaked, (
            f"{key} 的登记例外键 {leaked} 已出现在 seed 面——例外已清偿，"
            "须更新基线 registered_exceptions（含出口记录）而不是让登记失真"
        )
