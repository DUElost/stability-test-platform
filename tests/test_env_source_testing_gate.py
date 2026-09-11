"""#884（R01-F04）：TESTING=1 下不得加载 .env.backend / backend/.env。"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 按文件路径加载（不走 backend.core 包）：包级导入会触发 env_source 的配置
# 解析，而本测试刻意在无 DATABASE_URL 的环境下运行。
_spec = importlib.util.spec_from_file_location(
    "env_source_884", REPO_ROOT / "backend" / "core" / "env_source.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)
load_app_dotenv = _mod.load_app_dotenv


def _fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "backend").mkdir(parents=True)
    (root / ".env.backend").write_text(
        "STP_GATE_PROD_SENTINEL=from-prod-file\n", encoding="utf-8",
    )
    (root / "backend" / ".env").write_text(
        "STP_GATE_LOCAL_SENTINEL=from-local-file\n", encoding="utf-8",
    )
    return root


def test_testing_skips_dotenv_gate(tmp_path, monkeypatch):
    """TESTING=1：即使伪造的生产 env 文件存在，关键键也不注入进程。"""
    monkeypatch.setenv("TESTING", "1")
    monkeypatch.delenv("STP_GATE_PROD_SENTINEL", raising=False)
    monkeypatch.delenv("STP_GATE_LOCAL_SENTINEL", raising=False)
    root = _fake_repo(tmp_path)

    load_app_dotenv(root)

    assert "STP_GATE_PROD_SENTINEL" not in os.environ, "不得读取 .env.backend"
    assert "STP_GATE_LOCAL_SENTINEL" not in os.environ, "也不读本地 .env"


def test_non_testing_loads_dotenv(tmp_path, monkeypatch):
    """非测试环境：.env.backend 与本地 .env 正常加载（生产路径不回归）。"""
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("STP_GATE_PROD_SENTINEL", raising=False)
    monkeypatch.delenv("STP_GATE_LOCAL_SENTINEL", raising=False)
    root = _fake_repo(tmp_path)

    load_app_dotenv(root)

    assert os.environ["STP_GATE_PROD_SENTINEL"] == "from-prod-file"
    assert os.environ["STP_GATE_LOCAL_SENTINEL"] == "from-local-file"
    # 清理，避免污染同进程其他用例
    for key in ("STP_GATE_PROD_SENTINEL", "STP_GATE_LOCAL_SENTINEL"):
        os.environ.pop(key, None)
