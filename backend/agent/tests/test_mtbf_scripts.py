# -*- coding: utf-8 -*-
"""MTBF 脚本侧单元测试（backend/agent/scripts/mtbf_*）。

加载方式：importlib + sys.path 注入（脚本目录不是合法 Python 包路径，
对齐 test_flash_firmware.py 先例）。golden fixtures 与后端服务测试同源。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "mtbf"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def finish_lib():
    return _load("mtbf_finish_lib", "mtbf_finish/v1.2.0/_lib.py")


@pytest.fixture(scope="module")
def setup_lib():
    return _load("mtbf_setup_lib", "mtbf_setup/v1.2.0/_lib.py")


@pytest.fixture(scope="module")
def setup_mod():
    """mtbf_setup 入口模块（v1.3.0：adb root fail-fast 前置）。"""
    return _load("mtbf_setup_mod", "mtbf_setup/v1.3.0/mtbf_setup.py")


@pytest.fixture(scope="module")
def finish_mod():
    """mtbf_finish 入口模块（v1.3.0：adb pull 目录层级修正）。"""
    return _load("mtbf_finish_mod", "mtbf_finish/v1.3.0/mtbf_finish.py")


@pytest.fixture(scope="module")
def finish_mod_v14():
    """mtbf_finish v1.4.0：NFS JSON metrics 补 suite_sha256（与 init trace 闭环）。"""
    return _load("mtbf_finish_mod_v14", "mtbf_finish/v1.4.0/mtbf_finish.py")


@pytest.fixture(scope="module")
def check_mod():
    return _load("mtbf_check_mod", "mtbf_check/v1.2.0/mtbf_check.py")


@pytest.fixture(scope="module")
def check_mod_v13():
    """mtbf_check v1.3.0：expected 只读注入，env 预置退役（#404 PR-D）。"""
    return _load("mtbf_check_mod_v13", "mtbf_check/v1.3.0/mtbf_check.py")


@pytest.fixture()
def real_runtask() -> bytes:
    return (_FIXTURES / "runtask.xml").read_bytes()


@pytest.fixture()
def real_result() -> bytes:
    return (_FIXTURES / "realresult.xml").read_bytes()


# ---------------------------------------------------------------------------
# times patch（与后端服务同规则，golden 对齐）
# ---------------------------------------------------------------------------


class TestPatchTimes:
    def test_real_file_golden(self, finish_lib, real_runtask):
        patched = finish_lib.patch_runtask_times(real_runtask, 100)
        head = patched[:512]                       # CRLF 文件，根元素在首行之后
        assert b'times="100"' in head
        assert b'times="1000"' not in head
        # 仅 times 数字变化：1000(4字符) → 100(3字符)，其余字节不变
        assert len(patched) == len(real_runtask) - 1
        assert patched.replace(b'times="100"', b"") == real_runtask.replace(b'times="1000"', b"")

    def test_attributes_order_independent(self, setup_lib):
        content = b'<runtask name="t" stopWhenFail="false" times="42"><testpoint name="x"/></runtask>'
        assert b'times="9"' in setup_lib.patch_runtask_times(content, 9)

    @pytest.mark.parametrize("times", [0, -1])
    def test_non_positive_keeps_original(self, setup_lib, real_runtask, times):
        assert setup_lib.patch_runtask_times(real_runtask, times) == real_runtask


class TestCountTestpoints:
    def test_real_file(self, setup_lib, real_runtask):
        assert setup_lib.count_testpoints(real_runtask) == 130

    def test_bad_xml_zero(self, setup_lib):
        assert setup_lib.count_testpoints(b"<broken") == 0


# ---------------------------------------------------------------------------
# realresult 解析（schema 见 P0 设计 §2）
# ---------------------------------------------------------------------------


class TestParseRealresult:
    def test_summary_counts(self, finish_lib, real_result):
        parsed = finish_lib.parse_realresult(real_result)
        assert parsed["taskname"] == "模拟老化(仿RM老化+MTBF)_Trassion_2023_8_23"
        assert parsed["entries"] == 3
        assert parsed["passed"] == 1
        assert parsed["failed"] == 1
        assert parsed["error"] == 1

    def test_join_key_is_name_not_id(self, finish_lib, real_result):
        """id 恒为 0，join 键必须是 name（多轮次同名两条）。"""
        parsed = finish_lib.parse_realresult(real_result)
        names = [tp["name"] for tp in parsed["testpoints"]]
        assert names.count("关闭软件商店的Wian自动更新消息提醒开关") == 2   # 两轮
        assert names.count("重复打开Photos，传音项目") == 1
        entries = [tp for tp in parsed["testpoints"] if tp["name"] == "关闭软件商店的Wian自动更新消息提醒开关"]
        assert entries[0]["status"] == "PASS"     # 第一轮 pass
        assert entries[1]["status"] == "FAILURE"  # 第二轮 failure（回归重跑 regression=1）
        assert entries[1]["testcases"][0]["message"].startswith("assert failed")

    def test_any_testcase_failure_marks_testpoint(self, finish_lib, real_result):
        parsed = finish_lib.parse_realresult(real_result)
        photos = [tp for tp in parsed["testpoints"] if tp["name"] == "重复打开Photos，传音项目"][0]
        assert photos["status"] == "ERROR"        # 第二个 testcase 有 <error>
        assert photos["failures"] == "0"          # 落盘 failures 属性不可信，以子元素为准
        assert photos["testcases"][0]["status"] == "PASS"
        assert photos["testcases"][1]["status"] == "ERROR"

    def test_metadata_preserved(self, finish_lib, real_result):
        parsed = finish_lib.parse_realresult(real_result)
        tp = parsed["testpoints"][1]
        assert tp["regression"] == "1"
        assert tp["startbattery"] == "94"
        assert tp["stopbattery"] == "93"
        case = tp["testcases"][0]
        assert case["screenshot"].endswith(".png")
        assert case["method"] == "test_Reliability0141_CloseStoreWlan"

    def test_bad_xml_raises(self, finish_lib):
        with pytest.raises(ValueError):
            finish_lib.parse_realresult(b"<broken")


# ---------------------------------------------------------------------------
# mtbf_check 进度/存活逻辑（monkeypatch ADB）
# ---------------------------------------------------------------------------


class TestCheckProgress:
    def test_count_testpoints_grep_ok(self, check_mod, monkeypatch):
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(
            check_mod, "_adb_grep",
            lambda xml: (0, "37\n", ""),
        )
        assert check_mod._count_testpoints("2026.08.20_01.00.00.000") == 37

    def test_count_testpoints_grep_fallback_size(self, check_mod, monkeypatch):
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(check_mod, "_adb_grep", lambda xml: (1, "", "grep: not found"))
        monkeypatch.setattr(
            check_mod, "adb_shell",
            lambda cmd, timeout=30: "-rw-rw---- root sdcard_rw 160000 2026-08-20 01:00 /sdcard/results/realresult/x/TESTS-RealResult-TestPoints.xml",
        )
        assert check_mod._count_testpoints("x") == 400   # 160000 // 400

    def test_service_alive(self, check_mod, monkeypatch):
        monkeypatch.setattr(
            check_mod, "adb_shell",
            lambda cmd, timeout=30: "  ServiceRecord{xxx u0 com.ape.offlinescriptmanager/.view.RunTaskService}",
        )
        assert check_mod._service_alive() is True

    def test_run_dead_streak_grace(self, check_mod, monkeypatch, tmp_path):
        """连续 2 周期死亡才判死；第 1 周期仍 success。"""
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S2")
        monkeypatch.setattr(check_mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(check_mod, "_service_alive", lambda: False)
        monkeypatch.setattr(check_mod, "_latest_run_dir", lambda: "run1")
        monkeypatch.setattr(check_mod, "_count_testpoints", lambda run_dir: 10)
        monkeypatch.setattr(check_mod, "_log_bytes", lambda run_dir: 2048)
        monkeypatch.setattr(check_mod, "progress_stamp", lambda payload: None)

        r1 = check_mod._run({"expected_testpoint_count": 130, "dead_grace_cycles": 2})
        assert r1["success"] is True                     # 第 1 周期：容忍
        assert r1["progress"]["testpoints_done"] == 10
        r2 = check_mod._run({"expected_testpoint_count": 130, "dead_grace_cycles": 2})
        assert r2["success"] is False                    # 第 2 周期：判死
        assert "连续 2 个周期" in r2["error_message"]

    def test_run_alive_resets_streak(self, check_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S3")
        monkeypatch.setattr(check_mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(check_mod, "_latest_run_dir", lambda: "run1")
        monkeypatch.setattr(check_mod, "_count_testpoints", lambda run_dir: 5)
        monkeypatch.setattr(check_mod, "_log_bytes", lambda run_dir: 100)
        monkeypatch.setattr(check_mod, "progress_stamp", lambda payload: None)

        monkeypatch.setattr(check_mod, "_service_alive", lambda: False)
        check_mod._run({})
        monkeypatch.setattr(check_mod, "_service_alive", lambda: True)   # 看门狗拉起
        r2 = check_mod._run({})
        assert r2["success"] is True
        assert r2["progress"]["seq"] == 2


# ---------------------------------------------------------------------------
# mtbf_check v1.3.0 env 退役（ADR-0030 P1 设计 §3.4，#404 PR-D）
# ---------------------------------------------------------------------------


class TestCheckV13ParamsOnlyExpected:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, done=10):
        monkeypatch.setattr(mod, "device_serial", lambda: "S13")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "_service_alive", lambda: True)
        monkeypatch.setattr(mod, "_latest_run_dir", lambda: "run1")
        monkeypatch.setattr(mod, "_count_testpoints", lambda run_dir: done)
        monkeypatch.setattr(mod, "_log_bytes", lambda run_dir: 100)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_injected_param_still_wins(self, check_mod_v13, monkeypatch, tmp_path):
        self._patch_device_io(check_mod_v13, monkeypatch, tmp_path)
        r = check_mod_v13._run({"expected_testpoint_count": 130})
        assert r["success"] is True
        assert r["progress"]["expected_per_round"] == 130

    def test_env_fallback_removed(self, check_mod_v13, monkeypatch, tmp_path):
        """v1.2.0 会回落 STP_MTBF_EXPECTED_TESTPOINT_COUNT；v1.3.0 忽略之
        （host .env 里可能残留退役前的值，不得再当基准）。"""
        self._patch_device_io(check_mod_v13, monkeypatch, tmp_path)
        monkeypatch.setenv("STP_MTBF_EXPECTED_TESTPOINT_COUNT", "999")
        r = check_mod_v13._run({})
        assert r["progress"]["expected_per_round"] == 0   # 只报绝对数

    def test_missing_param_reports_absolute_only(self, check_mod_v13, monkeypatch, tmp_path):
        """无绑定 Plan 无注入 → expected=0，脚本语义 = 只报绝对数（安全降级）。"""
        self._patch_device_io(check_mod_v13, monkeypatch, tmp_path)
        monkeypatch.delenv("STP_MTBF_EXPECTED_TESTPOINT_COUNT", raising=False)
        r = check_mod_v13._run({})
        assert r["progress"]["expected_per_round"] == 0
        assert r["progress"]["testpoints_done"] == 10


# ---------------------------------------------------------------------------
# 参数解析约定（params > env > 代码默认；default_params 恒为空）
# ---------------------------------------------------------------------------


class TestParamOrEnv:
    def test_params_win(self, setup_lib, monkeypatch):
        monkeypatch.setenv("STP_MTBF_PROJECT", "env-proj")
        assert setup_lib.param_or_env({"project": "cfg-proj"}, "project", "STP_MTBF_PROJECT", "d") == "cfg-proj"

    def test_env_fallback(self, setup_lib, monkeypatch):
        monkeypatch.setenv("STP_MTBF_PROJECT", "env-proj")
        assert setup_lib.param_or_env({}, "project", "STP_MTBF_PROJECT", "d") == "env-proj"

    def test_code_default(self, setup_lib, monkeypatch):
        monkeypatch.delenv("STP_MTBF_PROJECT", raising=False)
        assert setup_lib.param_or_env({}, "project", "STP_MTBF_PROJECT", "legacy") == "legacy"


# ---------------------------------------------------------------------------
# mtbf_setup v1.3.0 adb root 前置（fail-fast；v1.2.0 曾忽略失败 → push rc=1）
# ---------------------------------------------------------------------------


class TestEnsureAdbRoot:
    def test_root_ok(self, setup_mod, monkeypatch):
        monkeypatch.setattr(setup_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(setup_mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(
            setup_mod, "adb", lambda *a, **k: (0, "restarting adbd as root\n", "")
        )
        monkeypatch.setattr(
            setup_mod, "adb_shell", lambda cmd, timeout=30: "0\n" if cmd == "id -u" else ""
        )
        setup_mod._ensure_adb_root()  # 不抛异常

    def test_root_denied_fails_fast_with_build_diagnostics(self, setup_mod, monkeypatch):
        monkeypatch.setattr(setup_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(setup_mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(
            setup_mod, "adb",
            lambda *a, **k: (0, "adbd cannot run as root in production builds\n", ""),
        )
        replies = {
            "id -u": "2000\n",
            "getprop ro.build.type": "user\n",
            "getprop ro.debuggable": "0\n",
        }
        monkeypatch.setattr(
            setup_mod, "adb_shell", lambda cmd, timeout=30: replies.get(cmd, "")
        )
        with pytest.raises(RuntimeError) as ei:
            setup_mod._ensure_adb_root()
        msg = str(ei.value)
        assert "adb root" in msg
        assert "ro.build.type=user" in msg
        assert "ro.debuggable=0" in msg
        assert "2000" in msg
        assert "userdebug/eng" in msg

    def test_root_retries_through_adbd_restart_window(self, setup_mod, monkeypatch):
        """adbd 重启窗口内 id -u 为空 → 重试后判定为 root，不误报。"""
        monkeypatch.setattr(setup_mod.time, "sleep", lambda s: None)
        monkeypatch.setattr(setup_mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(
            setup_mod, "adb", lambda *a, **k: (0, "restarting adbd as root\n", "")
        )
        calls = {"n": 0}

        def fake_shell(cmd, timeout=30):
            if cmd == "id -u":
                calls["n"] += 1
                return "" if calls["n"] <= 2 else "0\n"
            return ""

        monkeypatch.setattr(setup_mod, "adb_shell", fake_shell)
        setup_mod._ensure_adb_root()
        assert calls["n"] >= 3


# ---------------------------------------------------------------------------
# mtbf_finish v1.3.0 adb pull 目录层级（冒烟 #217 实测：<local>/realresult/{run_dir}/）
# ---------------------------------------------------------------------------


class TestPullResults:
    def test_pull_layout_has_realresult_level(self, finish_mod, monkeypatch):
        """adb pull 目录保留远端末级名：<local>/realresult/{run_dir}/。"""
        monkeypatch.setattr(finish_mod, "_latest_run_dir", lambda: "R1")

        def fake_adb(*args, timeout=60):
            local = Path(args[2])
            (local / "realresult" / "R1").mkdir(parents=True)
            (local / "realresult" / "R1" / "TESTS-RealResult-TestPoints.xml").write_text(
                "<testpoints/>"
            )
            return (0, "", "")

        monkeypatch.setattr(finish_mod, "adb", fake_adb)
        run_dir, xml_dir = finish_mod._pull_results()
        assert run_dir == "R1"
        assert (xml_dir / "TESTS-RealResult-TestPoints.xml").is_file()

    def test_pull_fallback_flat_layout(self, finish_mod, monkeypatch):
        """兜底：个别 adb 版本 dest 不存在时直接展开到 <local>/{run_dir}/。"""
        monkeypatch.setattr(finish_mod, "_latest_run_dir", lambda: "R2")

        def fake_adb(*args, timeout=60):
            local = Path(args[2])
            (local / "R2").mkdir(parents=True)
            (local / "R2" / "TESTS-RealResult-TestPoints.xml").write_text(
                "<testpoints/>"
            )
            return (0, "", "")

        monkeypatch.setattr(finish_mod, "adb", fake_adb)
        _, xml_dir = finish_mod._pull_results()
        assert (xml_dir / "TESTS-RealResult-TestPoints.xml").is_file()


# ---------------------------------------------------------------------------
# mtbf_finish v1.4.0 suite_sha256（结果 JSON 与 init trace 闭环）
# ---------------------------------------------------------------------------


class TestFinishSuiteSha256:
    def test_run_metrics_include_suite_sha256(self, finish_mod_v14, monkeypatch, tmp_path):
        xml = b"""<testpoints taskname="t">
  <testpoint id="0" name="a" tests="1" failures="0" time="1" starttime="0" endtime="1">
    <testcase type="uiautomator2" classname="c" name="m" time="1" starttime="0" endtime="1"/>
  </testpoint>
</testpoints>"""
        monkeypatch.setattr(finish_mod_v14, "_stop_task", lambda force=True: None)
        monkeypatch.setattr(finish_mod_v14.time, "sleep", lambda _: None)

        def fake_pull():
            d = tmp_path / "realresult" / "R1"
            d.mkdir(parents=True)
            (d / "TESTS-RealResult-TestPoints.xml").write_bytes(xml)
            return "R1", d

        monkeypatch.setattr(finish_mod_v14, "_pull_results", fake_pull)

        nfs = tmp_path / "nfs" / "legacy"
        nfs.mkdir(parents=True)
        runtask = nfs / "runtask.xml"
        runtask.write_bytes(b"<runtask times=\"1\"/>")
        results = tmp_path / "nfs" / "legacy" / "results"
        monkeypatch.setattr(finish_mod_v14, "suite_dir", lambda project: nfs)
        monkeypatch.setattr(finish_mod_v14, "results_dir", lambda project: results)
        monkeypatch.setattr(
            finish_mod_v14,
            "sha256_file",
            lambda p: "abc123" if p == runtask else "",
        )

        out = finish_mod_v14._run({"project": "legacy"})
        assert out["metrics"]["suite_sha256"] == "abc123"
        detail = results / "R1.json"
        assert detail.is_file()
        import json

        body = json.loads(detail.read_text(encoding="utf-8"))
        assert body["metrics"]["suite_sha256"] == "abc123"


# ---------------------------------------------------------------------------
# mtbf_check PROGRESS 打戳格式（#115 契约）
# ---------------------------------------------------------------------------


class TestProgressStamp:
    def test_stderr_line(self, finish_lib, capsys):
        finish_lib.progress_stamp({"seq": 1, "step": "mtbf_check"})
        captured = capsys.readouterr()
        assert captured.err.strip() == 'PROGRESS {"seq": 1, "step": "mtbf_check"}'
        assert captured.out == ""
