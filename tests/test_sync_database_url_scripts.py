"""同步 engine 取串守卫（#2471）。

`resolve_database_url()` 返回的是**异步驱动**串（dev compose 与控制面 `.env.backend` 均为
`postgresql+asyncpg://`）。把它原样交给同步 `create_engine()`，首次连接即炸
`MissingGreenlet`——而崩栈与判红同为 exit 1，人读到的是「门禁发现了问题」，实际根本没比。
同族已炸过两次（#735 §1.3 的 `check_unreferenced_script_versions.py`、#2471 的
`check_seed_identity.py`），第三次靠人肉 grep 不可靠，故立此静态守卫。

判据形状与治理面 S5x 同：**登记表强制回答**——新取串点要么用共享写法，要么在表里留下
可核验证据（证据串本身也被断言，防止豁免只剩一句注释）。纯文本扫描，不 import backend、
不需要数据库。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("backend", "tools", "scripts")
SKIP_PARTS = {".wt", "node_modules", "venv", "__pycache__"}

# 共享写法：`create_engine(normalize_sync_database_url(...))`
NEEDS_HELPER = "create_engine(normalize_sync_database_url("

# 用共享写法的取串点（在表里只为让「覆盖面」可核对——它们不享受证据豁免）
HELPER_SITES = (
    "backend/scripts/check_seed_identity.py",
    "backend/scripts/check_unreferenced_script_versions.py",
    "backend/scripts/compute_host_script_targets.py",
    "tools/dev/backfill-no-scan-gate-upload-state.py",
    "tools/dev/backfill-test-project.py",
    "tools/dev/retire_script_versions.py",
)

# 例外表：值 = 该文件「确实自己做了同步归一化」的可核验证据串（证据消失即红）。
EXEMPT = {
    "backend/core/database.py": (
        "def normalize_sync_database_url(",
        "_sync_url = normalize_sync_database_url(",
    ),
    "backend/alembic/env.py": (
        '_sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")',
    ),
    "backend/scripts/check_schema_sync.py": (
        're.sub(r"\\+asyncpg", "+psycopg", db_url)',
    ),
}


def _sources() -> dict[str, str]:
    out: dict[str, str] = {}
    for sub in SCAN_DIRS:
        base = REPO_ROOT / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            if SKIP_PARTS & set(rel.parts):
                continue
            out[rel.as_posix()] = path.read_text(encoding="utf-8")
    return out


def _sync_engine_sites(sources: dict[str, str]) -> list[tuple[str, str]]:
    """「同文件既取 DATABASE_URL、又建同步 engine」的取串点。"""
    return [
        (rel, text)
        for rel, text in sources.items()
        if "resolve_database_url(" in text and "create_engine(" in text
    ]


def test_sync_engine_sites_are_known():
    """覆盖面不能无声扩大，也不能长期悬空。"""
    found = {rel for rel, _ in _sync_engine_sites(_sources())}
    declared = set(EXEMPT) | set(HELPER_SITES)
    unknown = sorted(found - declared)
    assert unknown == [], (
        f"这些模块同时使用 resolve_database_url() 与 create_engine()，却未在守卫登记表里：{unknown}。"
        "先确认它没有把异步驱动串交给同步 engine（会炸 MissingGreenlet，且崩栈与判红同为 exit 1）："
        "改用 create_engine(normalize_sync_database_url(url))，或在本文件的 EXEMPT 里登记可核验证据。"
    )
    vanished = sorted(declared - found)
    assert vanished == [], f"登记表里的取串点已不在场，请同步删项（豁免不得悬空）：{vanished}"


def test_sync_engine_urls_use_shared_normalizer_or_evidence():
    bad: list[str] = []
    for rel, text in _sync_engine_sites(_sources()):
        if rel in EXEMPT:
            missing = [ev for ev in EXEMPT[rel] if ev not in text]
            if missing:
                bad.append(f"{rel}: 自有归一化的证据串不再在场 {missing}")
            continue
        if NEEDS_HELPER not in text:
            bad.append(f"{rel}: 未把 resolve_database_url() 的结果过 normalize_sync_database_url()")
    assert not bad, "同步 engine 取串未归一化驱动（#2471）：" + "；".join(bad)


def test_seed_identity_regression_anchor():
    """#2471 本尊：按 docstring 在 dev/控制机手工跑门禁，不得炸在连接阶段。"""
    text = (REPO_ROOT / "backend/scripts/check_seed_identity.py").read_text(encoding="utf-8")
    assert NEEDS_HELPER in text
    assert "create_engine(url," not in text and "create_engine(url)" not in text, (
        "check_seed_identity 又退回把原始串交给同步 engine（#2471）"
    )
