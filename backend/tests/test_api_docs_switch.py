"""G22: STP_API_DOCS_ENABLED 开关（/docs /redoc /openapi.json 同开同关）。

- 解析语义：白名单 1/true/yes/on（容忍空白、大小写不敏感）为开；
  未设置/空串 = 缺省开；其余一律关（与 core/security.py 同款白名单风格）。
- 默认态端到端：/openapi.json 与 /docs 在真实 app 上可达（现状行为不变）。
  关闭态是进程级配置（FastAPI 构造参数在 import 时定格），端到端复验
  走部署机 curl（见同 PR Agent Note），不在套件内起子进程 uvicorn。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import _api_docs_enabled, fastapi_app


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("YES", True),
        ("On", True),
        ("", True),  # 显式空串视为未配置 → 缺省开
        ("  on  ", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("OFF", False),
        ("  off  ", False),
        ("2", False),  # 白名单外的任何值一律关——文档面宁可不暴露
    ],
)
def test_api_docs_enabled_parsing(raw: str, expected: bool):
    assert _api_docs_enabled(raw) is expected


def test_default_is_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("STP_API_DOCS_ENABLED", raising=False)
    assert _api_docs_enabled() is True


def test_docs_routes_reachable_by_default():
    # conftest 不设置 STP_API_DOCS_ENABLED —— 测试环境即缺省开。
    client = TestClient(fastapi_app)
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200
