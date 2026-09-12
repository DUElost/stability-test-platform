"""#1664：CI 接线 × 测试库护栏的契约守卫（PR 路径）。

背景：#1547 夜间 `backend-test` 被 `backend/tests/conftest.py` 导入期的
`db_url_guard` 拒载（`TEST_DATABASE_URL` 与 `DATABASE_URL` 同库 → pytest exit 4，
同 job 的 agent / 仓库级测试因 `if: success()` 全部 skipped）。接线本身已由
PR #1566 修好，但**该类回归在 PR 路径上仍零覆盖**——`pr-agent-tests` 与
`pr-migrate-empty-db` 都不 import `backend/tests/conftest.py`，`db_url_guard`
的单测又在只在夜间跑的 `backend/tests/core/` 下。

本文件把该契约锚在 PR 路径上，覆盖两个方向：

1. **护栏语义**：同库必须拒载、不同库放行、库名不含 test 必须拒载——直接测
   `db_url_guard` 纯函数（不 import conftest，故不触发 testcontainers）；
2. **CI 接线**：`ci.yml` 的 `backend-test` job 级 env **不得**出现 `DATABASE_URL`
   （它与 `TEST_DATABASE_URL` 同库，正是 #1547 的拒载成因），且 `TEST_DATABASE_URL`
   必须就位。

第 2 点是本文件的主力：它把「撤销 #1566 那一行」这类改动变成 PR 可拦的红灯，
而不是只能等下一次夜间全量。纯离线、无 docker、无 DB（见 tests/README 判据）。
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD_PATH = REPO_ROOT / "backend" / "core" / "db_url_guard.py"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_guard():
    """按文件路径加载 db_url_guard，避免 import backend 包触发其它副作用。"""
    spec = importlib.util.spec_from_file_location("db_url_guard_under_test", GUARD_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["db_url_guard_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


_guard = _load_guard()

_SAME_DB = "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test"
_OTHER_DB = "postgresql+psycopg://postgres:postgres@localhost:5432/stability_test_e2e"


class TestGuardSemantics:
    """护栏本身的行为（#1547 的直接成因）。"""

    def test_identical_runtime_url_is_rejected(self):
        """同库拒载——#1547 的报错本体。"""
        with pytest.raises(_guard.UnsafeTestDatabaseUrl, match="identical"):
            _guard.guard_test_database_url(_SAME_DB, runtime_database_url=_SAME_DB)

    def test_distinct_runtime_url_is_allowed(self):
        """不同库放行（库名含 test，满足隔离命名约定）。"""
        assert (
            _guard.guard_test_database_url(_OTHER_DB, runtime_database_url=_SAME_DB)
            == _OTHER_DB
        )

    def test_absent_runtime_url_is_allowed(self):
        """未提供运行时 URL 时不做同库判定——这正是 #1566 采用的 CI 接线形态。"""
        assert _guard.guard_test_database_url(_SAME_DB, runtime_database_url=None) == _SAME_DB
        assert _guard.guard_test_database_url(_SAME_DB, runtime_database_url="") == _SAME_DB

    def test_dbname_without_test_is_rejected(self):
        """库名不含 test → 拒载（隔离命名约定的另一半）。"""
        bad = "postgresql+psycopg://postgres:postgres@localhost:5432/stability_app"
        with pytest.raises(_guard.UnsafeTestDatabaseUrl, match="must contain 'test'"):
            _guard.guard_test_database_url(bad, runtime_database_url=None)

    def test_non_postgres_scheme_is_rejected(self):
        with pytest.raises(_guard.UnsafeTestDatabaseUrl, match="must be a PostgreSQL URL"):
            _guard.guard_test_database_url("mysql://u:p@h/db_test", runtime_database_url=None)


def _backend_test_job_env_keys() -> set[str]:
    """取出 ci.yml 中 `backend-test` job 的**job 级** env 键名。

    只解析缩进到 job 直属层级的 `env:` 块——步骤级 env（alembic migrate /
    agent tests 两处刻意注入 DATABASE_URL）不在其中，它们正是 #1566 的正确形态。
    """
    lines = CI_YML.read_text(encoding="utf-8").splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if re.match(r"^  backend-test:\s*$", ln))
    except StopIteration:  # pragma: no cover - ci.yml 结构被改名时明确报错
        raise AssertionError("ci.yml 中未找到 backend-test job（结构被改名？）") from None

    # job 块在下一个同为 2 空格缩进的键处结束
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^  \S", lines[i]):
            end = i
            break

    keys: set[str] = set()
    in_env = False
    for ln in lines[start:end]:
        # job 直属的 `    env:`（4 空格）开启块；更深缩进属步骤级，不在此列
        if re.match(r"^    env:\s*$", ln):
            in_env = True
            continue
        if in_env:
            if re.match(r"^    \S", ln) and not ln.startswith("      "):
                in_env = False
                continue
            m = re.match(r"^      ([A-Z][A-Z0-9_]*):", ln)
            if m:
                keys.add(m.group(1))
    return keys


class TestCiWiring:
    """ci.yml 接线契约——#1547 的防复发主守卫。"""

    def test_backend_test_has_no_job_level_database_url(self):
        keys = _backend_test_job_env_keys()
        assert "TEST_DATABASE_URL" in keys, "backend-test 必须提供 TEST_DATABASE_URL"
        assert "DATABASE_URL" not in keys, (
            "backend-test 的 job 级 env 不得提供 DATABASE_URL——它与 TEST_DATABASE_URL "
            "同库，会让 backend/tests/conftest.py 导入期触发 db_url_guard 同库拒载"
            "（#1547，pytest exit 4，同 job 后续测试全部 skipped）。确需 DATABASE_URL "
            "的步骤（alembic migrate / agent tests）请用步骤级 env 注入。"
        )

    def test_ci_yml_is_parseable_and_job_found(self):
        """防止上面的结构解析静默失效（改名/重排后仍应找得到 job）。"""
        assert _backend_test_job_env_keys(), "backend-test job 级 env 解析为空，解析器已失效"
