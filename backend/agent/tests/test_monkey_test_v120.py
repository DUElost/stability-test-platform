"""monkey_test v1.2.0 的 #507 三要素增量：路由决策进 metrics.route + AD11 asset 缺失 fail-fast。

v1.1.0 的推送/启动行为在此不重复验证；只测路由块：
- AD11 机型 + 专属脚本存在 → route.branch == "AD11"
- AD11 机型 + 专属脚本缺失 → fail-fast（success=False，不静默回退）
- 普通机型 → route.branch == "generic"
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SCRIPT_DIR = (
    Path(__file__).resolve().parents[2]
    / "agent" / "scripts" / "monkey_test" / "v1.2.0"
)

# _adb.py 是版本目录内辅助模块——importlib 加载脚本前先入 path
sys.path.insert(0, str(_SCRIPT_DIR))

spec = importlib.util.spec_from_file_location(
    "monkey_test_v120", _SCRIPT_DIR / "monkey_test.py"
)
mt = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(mt)


@pytest.fixture
def aimonkey_dir(tmp_path: Path) -> Path:
    """AIMonkey 资源目录：通用 + AD11 专属脚本 + 二进制骨架。"""
    d = tmp_path / "AIMonkeyTest"
    d.mkdir()
    for f in ("aim", "aim.jar", "aimonkey.apk", "MonkeyTestAi.sh",
              "MonkeyTestAiAD11.sh", "blacklist.txt"):
        (d / f).write_text("x", encoding="utf-8")
    (d / "aimwd").mkdir()
    return d


def _run(monkeypatch, aimonkey_dir: Path, model: str) -> dict:
    """执行 main() 并捕获 output_result 的 payload。"""
    captured: dict = {}

    def fake_output_result(success, error_message=None, metrics=None):
        captured.update({
            "success": success,
            "error_message": error_message,
            "metrics": metrics or {},
        })

    monkeypatch.setattr(mt, "device_serial", lambda: "SERIAL1")
    monkeypatch.setattr(mt, "params", lambda: {"aimonkey_dir": str(aimonkey_dir)})
    monkeypatch.setattr(mt, "output_result", fake_output_result)
    monkeypatch.setattr(
        mt, "_shell",
        lambda _serial, cmd, **k: (0, model if "ro.product.model" in cmd else "ok"),
    )
    monkeypatch.setattr(mt, "_push_file", lambda *a, **k: True)
    mt.main()
    return captured


def test_ad11_route_recorded(monkeypatch, aimonkey_dir: Path):
    out = _run(monkeypatch, aimonkey_dir, model="AD11-TEST")
    assert out["success"] is True
    route = out["metrics"]["route"]
    assert route == {
        "decided_by": "fingerprint",
        "model": "AD11-TEST",
        "branch": "AD11",
    }


def test_generic_route_recorded(monkeypatch, aimonkey_dir: Path):
    out = _run(monkeypatch, aimonkey_dir, model="MLD_LX3")
    assert out["success"] is True
    assert out["metrics"]["route"]["branch"] == "generic"
    assert out["metrics"]["route"]["model"] == "MLD_LX3"


def test_ad11_script_missing_fails_fast(monkeypatch, aimonkey_dir: Path):
    (aimonkey_dir / "MonkeyTestAiAD11.sh").unlink()
    out = _run(monkeypatch, aimonkey_dir, model="AD11-TEST")
    assert out["success"] is False
    assert "refusing silent fallback" in out["error_message"]
    # 失败即返回——通用脚本不被推送
    assert out["metrics"] == {}
