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


def test_new_versions_exist_and_old_retained_1715():
    assert (_SD / "mtbf_setup/v1.4.1/mtbf_setup.py").is_file()
    # 旧版本仍在（新行为以新版本表达）
    assert (_SD / "mtbf_setup/v1.4.0/mtbf_setup.py").is_file()


def _load_setup(version_dir: Path, name: str):
    """以文件方式加载 mtbf_setup 入口（部署形态一致；sys.path 供 import _lib）。"""
    import importlib.util
    import sys

    sys.path.insert(0, str(version_dir))
    try:
        spec = importlib.util.spec_from_file_location(name, version_dir / "mtbf_setup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(version_dir))
        sys.modules.pop("_lib", None)


def _fake_adb_shell(dirs: dict):
    """伪造设备侧 realresult 目录：dirs: name -> mtime（设备时钟语义）。"""

    def fake(cmd: str, timeout: int = 30) -> str:
        if cmd.strip() == "ls /sdcard/results/realresult/":
            return "\n".join(dirs) if dirs else ""
        if cmd.startswith("stat -c %Y"):
            name = cmd.rsplit("/", 1)[-1]
            return str(dirs.get(name, 0))
        return ""

    return fake


class TestSnapshotFreshnessV141:
    """#1715：run_dir 新鲜度判据改为「启动前快照对比」——设备 mtime 与快照
    同源自比，v1.4.0 的宿主 time.time() × 设备 mtime 跨钟比较（2s 容差）
    在设备时钟偏差 >2s 时把启动成功误判为 60s 超时。"""

    @pytest.fixture(scope="class")
    def setup_v141(self):
        return _load_setup(_SD / "mtbf_setup/v1.4.1", "mtbf_setup_v141")

    @pytest.fixture
    def no_sleep(self, monkeypatch, setup_v141):
        import time as _time

        class _FakeTime:
            sleep = staticmethod(lambda _s: None)
            time = staticmethod(_time.time)

        # 只替换模块引用，不动全局 time 模块
        monkeypatch.setattr(setup_v141, "time", _FakeTime)

    def test_new_dir_detected_despite_device_clock_skew(
        self, setup_v141, monkeypatch, no_sleep
    ):
        """核心回归：设备时钟远落后宿主时，新建目录仍须命中。"""
        dirs = {"stale_2026.09.12": 1000}  # 设备 mtime 语义（远早于宿主 epoch）
        monkeypatch.setattr(setup_v141, "adb_shell", _fake_adb_shell(dirs))
        before = setup_v141._snapshot_run_dirs()
        assert before == {"stale_2026.09.12": 1000.0}

        dirs["2026.09.13_10.00.00.000"] = 100  # 新目录，设备 mtime 远早于宿主 now
        result = setup_v141._wait_run_dir(timeout_s=30, before=before)
        assert result == "2026.09.13_10.00.00.000"

    def test_same_name_rebuild_detected_by_mtime_increase(
        self, setup_v141, monkeypatch, no_sleep
    ):
        dirs = {"run_2026.09.13": 1000}
        monkeypatch.setattr(setup_v141, "adb_shell", _fake_adb_shell(dirs))
        before = setup_v141._snapshot_run_dirs()

        dirs["run_2026.09.13"] = 2000  # 同名重建：mtime 相对快照增大
        result = setup_v141._wait_run_dir(timeout_s=30, before=before)
        assert result == "run_2026.09.13"

    def test_stale_untouched_dir_never_matched(
        self, setup_v141, monkeypatch, no_sleep
    ):
        dirs = {"stale_2026.09.12": 1000}
        monkeypatch.setattr(setup_v141, "adb_shell", _fake_adb_shell(dirs))
        before = setup_v141._snapshot_run_dirs()

        assert setup_v141._wait_run_dir(timeout_s=1, before=before) == ""

    def test_snapshot_stat_failure_treated_as_stale(
        self, setup_v141, monkeypatch, no_sleep
    ):
        dirs = {"weird_dir": 0}  # stat 读数 0 → 快照记 0.0（旧目录）
        monkeypatch.setattr(setup_v141, "adb_shell", _fake_adb_shell(dirs))
        before = setup_v141._snapshot_run_dirs()
        assert before == {"weird_dir": 0.0}
        # mtime 恒 0：不 > 快照值 0.0 → 不命中（与「新建后任意读数 > 0」区分）
        assert setup_v141._wait_run_dir(timeout_s=1, before=before) == ""


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
