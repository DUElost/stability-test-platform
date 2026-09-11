"""Agent 测试环境隔离护栏（R15-F03 / #1295）。

直接运行 `python -m pytest backend/agent/tests/...` 时，导入 backend.agent.*
不得回落到仓库根 `.env.backend`（生产唯一事实源）。conftest 在导入 agent 模块
前已 `setdefault` 安全的 DATABASE_URL；本测试把它钉成不变量。
"""
from __future__ import annotations

import os
from pathlib import Path

# 与 conftest 常量同源；此处独立复核实际生效值。
_EXPECTED_FALLBACK = (
    "postgresql+psycopg://agent_test:agent_test@127.0.0.1:5432/agent_test"
)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _production_env_url() -> str | None:
    env_file = _REPO_ROOT / ".env.backend"
    if not env_file.is_file():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def test_agent_tests_never_load_production_database_url():
    """生效的 DATABASE_URL 不得等于 .env.backend 里的生产地址。"""
    prod = _production_env_url()
    current = os.environ.get("DATABASE_URL", "")
    assert current, "conftest 必须为 Agent 测试提供非空 DATABASE_URL"
    if prod:
        assert current != prod, (
            "Agent 测试回落到生产 .env.backend 的 DATABASE_URL；"
            "conftest 环境隔离失效。"
        )


def test_agent_tests_without_ambient_url_use_local_placeholder(monkeypatch):
    """没有外部注入时，兜底值必须是本地隔离占位（非生产）。"""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # 重新计算 conftest 采用的兜底逻辑（同一常量）
    from backend.agent.tests import conftest as agent_conftest

    assert agent_conftest.AGENT_TEST_DATABASE_URL == _EXPECTED_FALLBACK
    assert "@127.0.0.1" in agent_conftest.AGENT_TEST_DATABASE_URL
