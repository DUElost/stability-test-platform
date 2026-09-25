"""结构日报脚本的离线自证（纯函数部分；git 读取面由脚本自身的真实运行覆盖）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "tools" / "dev" / "structure_digest.py"


def _load():
    spec = importlib.util.spec_from_file_location("structure_digest", _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_self_test_passes():
    assert _load()._self_test() == 0


def test_contract_baseline_matches_repo_config():
    mod = _load()
    text = (_PATH.parents[2] / ".importlinter").read_text(encoding="utf-8")
    baseline = mod.contract_baseline(text)
    assert set(baseline) == {
        "c1-layers", "c2-services-no-http", "c3-cp-not-into-agent",
        "c4-core-no-web", "c5-services-acyclic",
    }
    assert all(count >= 0 for count in baseline.values())
