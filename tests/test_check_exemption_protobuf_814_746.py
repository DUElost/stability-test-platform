"""#814 / #746 设备脚本回归：check 判死离线豁免 + gpu_check protobuf 字段级解析。

脚本以文件方式加载（部署形态一致），加载前把脚本目录加入 sys.path 以便
`import _lib`（与设备端运行布局一致）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SD = _REPO_ROOT / "backend/agent/scripts"


def _load(script_path: Path, name: str):
    script_dir = str(script_path.parent)
    sys.path.insert(0, script_dir)
    try:
        spec = importlib.util.spec_from_file_location(name, script_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(script_dir)
        sys.modules.pop("_lib", None)


@pytest.fixture(scope="module")
def gpu():
    return _load(_SD / "gpu_check/v1.0.8/gpu_check.py", "gpu_check_v108")


class TestProtobufFieldParse:
    """#746：按长度前缀取值，不再 64B 窗口子串扫描。"""

    def test_last_false_wins_over_true_in_window(self, gpu):
        # 真实失败：最后一条 test_result=false；其后 64B 内出现 "true" 文本
        log = b"\x12\x04true ... stacktrace expected <true> but was <false>" + \
              b"test_result" + b"\x12\x05false"
        assert gpu._last_protobuf_test_result(log) is False

    def test_last_true(self, gpu):
        log = b"test_result" + b"\x12\x04true"
        assert gpu._last_protobuf_test_result(log) is True

    def test_no_field_returns_none(self, gpu):
        assert gpu._last_protobuf_test_result(b"no result here") is None

    def test_length_prefixed_false_not_confused_by_later_true(self, gpu):
        # 字段值 false 后紧跟无关 true 字节——必须按长度只取 false
        log = b"test_result\x12\x05false" + b"\x12\x04true"
        assert gpu._last_protobuf_test_result(log) is False


class TestOfflineExemption:
    def test_sleep_check_has_device_online_gate(self):
        src = (_SD / "sleep_check/v1.0.3/sleep_check.py").read_text(encoding="utf-8")
        assert "device_online" in src
        assert "device_online()" in src

    def test_gpu_check_has_device_online_gate(self):
        src = (_SD / "gpu_check/v1.0.8/gpu_check.py").read_text(encoding="utf-8")
        assert "device_online" in src
        assert "device_online()" in src

    def test_new_versions_exist_and_old_retained(self):
        assert (_SD / "sleep_check/v1.0.3/sleep_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.8/gpu_check.py").is_file()
        assert (_SD / "sleep_check/v1.0.2/sleep_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.7/gpu_check.py").is_file()
