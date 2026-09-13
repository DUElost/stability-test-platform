"""#814 / #746 设备脚本回归：check 判死离线豁免 + gpu_check protobuf 字段级解析。

脚本以文件方式加载（部署形态一致），加载前把脚本目录加入 sys.path 以便
`import _lib`（与设备端运行布局一致）。
"""
from __future__ import annotations

import importlib.util
import json
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
        assert (_SD / "sleep_check/v1.0.4/sleep_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.10/gpu_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.9/gpu_check.py").is_file()
        assert (_SD / "sleep_check/v1.0.3/sleep_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.8/gpu_check.py").is_file()
        assert (_SD / "sleep_check/v1.0.2/sleep_check.py").is_file()
        assert (_SD / "gpu_check/v1.0.7/gpu_check.py").is_file()


class TestOfflineExemptionNoKeyError:
    """#1693：#814 离线豁免分支不写键——新 Job 首拍（job_id 重置后 state
    仅含 job_id）遇设备离线走 ``pass``，判死比较对 ``state["dead_streak"]``
    的无条件读取抛 KeyError，被 main() 捕获报 failure——离线反而必判失败。

    v1.0.4 / v1.0.9 起离线分支把 dead_streak 归一入 state（保持现值、
    不累计）。测试以文件加载形态打桩 adb 依赖，驱动真实 ``_run``。
    """

    @pytest.fixture(scope="module")
    def sleep_v104(self):
        return _load(_SD / "sleep_check/v1.0.4/sleep_check.py", "sleep_check_v104")

    @pytest.fixture(scope="module")
    def gpu_v109(self):
        return _load(_SD / "gpu_check/v1.0.9/gpu_check.py", "gpu_check_v109")

    @staticmethod
    def _stub_sleep(monkeypatch, mod, tmp_path, *, online, alive, state=None):
        state_file = tmp_path / "sleep_state.json"
        if state is not None:
            state_file.write_text(json.dumps(state))
        monkeypatch.setattr(mod, "_state_file", lambda: state_file)
        monkeypatch.setattr(mod, "device_online", lambda: online)
        monkeypatch.setattr(mod, "service_alive", lambda: alive)
        monkeypatch.setattr(mod, "_run_finished", lambda: False)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: None)
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: 0)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)
        return state_file

    @staticmethod
    def _stub_gpu(monkeypatch, mod, tmp_path, *, online, alive, state=None):
        state_file = tmp_path / "gpu_state.json"
        if state is not None:
            state_file.write_text(json.dumps(state))
        monkeypatch.setattr(mod, "_state_file", lambda: state_file)
        monkeypatch.setattr(mod, "device_online", lambda: online)
        monkeypatch.setattr(mod, "instrument_alive", lambda: alive)
        monkeypatch.setattr(mod, "_run_finished", lambda log: (False, ""))
        monkeypatch.setattr(mod, "_early_crash_verdict", lambda log, rc_streak: None)
        monkeypatch.setattr(mod, "_read_log_cat", lambda: b"")
        monkeypatch.setattr(mod, "_grep_rounds_done", lambda: 0)
        monkeypatch.setattr(mod, "result_log_bytes", lambda: 0)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)
        return state_file

    def test_sleep_fresh_state_offline_first_poll_no_crash(
        self, sleep_v104, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_sleep(
            monkeypatch, sleep_v104, tmp_path, online=False, alive=False
        )
        result = sleep_v104._run({})
        assert result["success"] is True
        saved = json.loads(state_file.read_text())
        assert saved["dead_streak"] == 0  # 归一入 state，不累计

    def test_sleep_offline_keeps_existing_streak(
        self, sleep_v104, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_sleep(
            monkeypatch, sleep_v104, tmp_path, online=False, alive=False,
            state={"job_id": "job-1693", "dead_streak": 1, "seq": 3},
        )
        result = sleep_v104._run({})
        assert result["success"] is True
        saved = json.loads(state_file.read_text())
        assert saved["dead_streak"] == 1  # 离线不累计

    def test_sleep_online_recovery_accumulates_to_grace(
        self, sleep_v104, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_sleep(
            monkeypatch, sleep_v104, tmp_path, online=False, alive=False
        )
        assert sleep_v104._run({})["success"] is True
        # 设备恢复在线但服务未起：恢复正常累计，grace=2 用尽即判死
        self._stub_sleep(monkeypatch, sleep_v104, tmp_path, online=True, alive=False)
        assert sleep_v104._run({})["success"] is True
        result = sleep_v104._run({})
        assert result["success"] is False
        assert "连续 2 个周期未存活" in result["error_message"]

    def test_sleep_alive_resets_streak(self, sleep_v104, monkeypatch, tmp_path):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_sleep(
            monkeypatch, sleep_v104, tmp_path, online=True, alive=True,
            state={"job_id": "job-1693", "dead_streak": 2, "seq": 1},
        )
        result = sleep_v104._run({})
        assert result["success"] is True
        saved = json.loads(state_file.read_text())
        assert saved["dead_streak"] == 0

    def test_gpu_fresh_state_offline_first_poll_no_crash(
        self, gpu_v109, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_gpu(
            monkeypatch, gpu_v109, tmp_path, online=False, alive=False
        )
        result = gpu_v109._run({})
        assert result["success"] is True
        saved = json.loads(state_file.read_text())
        assert saved["dead_streak"] == 0

    def test_gpu_offline_keeps_existing_streak(
        self, gpu_v109, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_gpu(
            monkeypatch, gpu_v109, tmp_path, online=False, alive=False,
            state={"job_id": "job-1693", "dead_streak": 1, "seq": 3},
        )
        result = gpu_v109._run({})
        assert result["success"] is True
        saved = json.loads(state_file.read_text())
        assert saved["dead_streak"] == 1

    def test_gpu_online_recovery_accumulates_to_grace(
        self, gpu_v109, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("STP_JOB_ID", "job-1693")
        state_file = self._stub_gpu(
            monkeypatch, gpu_v109, tmp_path, online=False, alive=False
        )
        assert gpu_v109._run({})["success"] is True
        self._stub_gpu(monkeypatch, gpu_v109, tmp_path, online=True, alive=False)
        assert gpu_v109._run({})["success"] is True
        result = gpu_v109._run({})
        assert result["success"] is False
        assert "连续 2 个周期未存活" in result["error_message"]


# 2026-09-01 真机实证形态（test_gpu_power_sleep_resources.py 同源）：
# length=5，第 5 字节是下一字段的 tag 字节 \x0f（非空白，bytes.strip() 不去除）
REAL_MONITOR_SAMPLE = (
    b"GPU_RUN_START test_id=002 rounds=10\n"
    b"\x00\x01\x18\x02\"\x01\x01\x0a\x00\x00\x01\x00\x01\x00\x01\x01"
    b"test_result\x12\x05true\x0ftestcase_name\x12\x1etest_StressSpecial_GPUTest_002"
    b"GPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n"
)


class TestProtobufPrefixVerdictV1010:
    """#1695：v1.0.8/v1.0.9 的字段值**全等**判定对真机样本（length 多含一个
    tag 字节）恒返回 None → verdict ``no-tests`` → 正常完成被判空跑失败
    （PASS→FAIL 回归）。v1.0.10 改前缀判定；长度前缀仍在，#746 的「最后一条
    为准 + 不被后文 true 子串干扰」语义保持。"""

    @pytest.fixture(scope="class")
    def gpu_v1010(self):
        return _load(_SD / "gpu_check/v1.0.10/gpu_check.py", "gpu_check_v1010")

    @pytest.fixture(scope="class")
    def gpu_v109(self):
        return _load(_SD / "gpu_check/v1.0.9/gpu_check.py", "gpu_check_v109_reg")

    def test_v109_regression_documented(self, gpu_v109):
        """修复前行为存证：v1.0.9 对真机样本解析为 None。"""
        assert gpu_v109._last_protobuf_test_result(REAL_MONITOR_SAMPLE) is None
        assert gpu_v109._run_finished(REAL_MONITOR_SAMPLE) == (True, "no-tests")

    def test_v1010_real_sample_is_ok(self, gpu_v1010):
        assert gpu_v1010._last_protobuf_test_result(REAL_MONITOR_SAMPLE) is True
        assert gpu_v1010._run_finished(REAL_MONITOR_SAMPLE) == (True, "ok")

    def test_v1010_synthetic_len4_still_ok(self, gpu_v1010):
        log = b"test_result\x12\x04true\nGPU_RUN_END rc=0\n"
        assert gpu_v1010._run_finished(log) == (True, "ok")

    def test_v1010_last_false_wins_over_later_true(self, gpu_v1010):
        # #746 回归保持：长度定界 false 不被紧随的 true 字节干扰
        log = b"test_result\x12\x05false" + b"\x12\x04true"
        assert gpu_v1010._last_protobuf_test_result(log) is False

    def test_v1010_false_sample_not_flipped(self, gpu_v1010):
        log = (
            b"GPU_RUN_START test_id=002 rounds=10\n"
            b"test_result\x12\x05false\x0ftestcase_name\x12\x1etest_X"
            b"GPU_ROUND 1 rc=0\nGPU_RUN_END rc=0\n"
        )
        assert gpu_v1010._run_finished(log) == (True, "no-tests")
