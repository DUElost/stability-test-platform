"""mtbf 三件套 run_dir 绑定（#810）。

守什么：
- setup 把观测到的 run_dir 落为「绑定」，check/finish 只认绑定而非「最新目录」
  （残留目录不得让新任务假阳性 / 混入历史轮数据）；
- 绑定按设备 serial 键控、按项目匹配；清理由 finish 完成；
- 三件套新版本存在且旧版本保留（不可原地改）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SD = _REPO_ROOT / "backend/agent/scripts"


def _load_lib(version_dir: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, version_dir / "_lib.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_tmp(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_new_versions_exist_and_old_retained():
    assert (_SD / "mtbf_setup/v1.4.0/mtbf_setup.py").is_file()
    assert (_SD / "mtbf_check/v1.5.0/mtbf_check.py").is_file()
    assert (_SD / "mtbf_finish/v1.6.0/mtbf_finish.py").is_file()
    # 旧版本仍在（新行为以新版本表达）
    assert (_SD / "mtbf_setup/v1.3.0/mtbf_setup.py").is_file()
    assert (_SD / "mtbf_check/v1.4.0/mtbf_check.py").is_file()
    assert (_SD / "mtbf_finish/v1.5.0/mtbf_finish.py").is_file()


def test_marker_roundtrip_and_project_match(isolated_tmp, monkeypatch):
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-1")
    lib = _load_lib(_SD / "mtbf_setup/v1.4.0", "mtbf_lib_setup_810")

    lib.save_run_dir("projA", "2026.09.12_10.00.00.000")
    assert lib.load_run_dir("projA") == "2026.09.12_10.00.00.000"
    # 项目不符 → 不返回（避免跨项目误用残留绑定）
    assert lib.load_run_dir("projB") == ""

    lib.clear_run_dir()
    assert lib.load_run_dir("projA") == ""


def test_marker_is_keyed_by_device_serial(isolated_tmp, monkeypatch):
    lib = _load_lib(_SD / "mtbf_check/v1.5.0", "mtbf_lib_check_810")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-A")
    lib.save_run_dir("projA", "runA")
    monkeypatch.setenv("STP_DEVICE_SERIAL", "SER-B")
    assert lib.load_run_dir("projA") == ""
    lib.save_run_dir("projA", "runB")
    assert lib.load_run_dir("projA") == "runB"


def test_setup_writes_binding():
    src = (_SD / "mtbf_setup/v1.4.0/mtbf_setup.py").read_text(encoding="utf-8")
    assert "save_run_dir(" in src
    # 只接受本步骤起点之后新建的目录
    assert "newer_than" in src


def test_check_prefers_binding_with_fallback():
    src = (_SD / "mtbf_check/v1.5.0/mtbf_check.py").read_text(encoding="utf-8")
    assert "_bound_run_dir(" in src
    assert "load_run_dir(" in src
    # 回退路径留痕
    assert "mtbf_check_run_dir_unbound_fallback" in src


def test_finish_prefers_binding_and_clears():
    src = (_SD / "mtbf_finish/v1.6.0/mtbf_finish.py").read_text(encoding="utf-8")
    assert "load_run_dir(" in src
    assert "clear_run_dir()" in src
    assert "_pull_results(project)" in src
