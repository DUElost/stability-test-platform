# -*- coding: utf-8 -*-
"""Sleep 脚本侧单元测试（backend/agent/scripts/sleep_setup|check|finish，issue #462 P0a）。

加载方式：importlib + sys.path 注入（对齐 test_mtbf_scripts.py 先例）。
golden fixture：fixtures/sleep/sleep_test_result.txt（设备端行格式样本，
含时间戳前缀、wake FAIL、灭屏失败 screen=ON 异常、finished 收尾）。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "sleep"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, rel_path: str):
    path = _SCRIPTS / rel_path
    sys.path.insert(0, str(path.parent))
    try:
        # 同名 `_lib` 辅助模块：不同脚本家族（mtbf vs sleep）目录下内容不同，
        # 但都以 `_lib` 模块名注册进 sys.modules——加载前清缓存避免串家族
        # （test_mtbf_scripts.py 先跑会把 mtbf 的 _lib 留下），加载后清掉
        # 避免把 sleep 的 _lib 留给后续测试（entry 模块内引用在 exec 时已绑定）。
        sys.modules.pop("_lib", None)
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot locate {path}"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.modules.pop("_lib", None)
        sys.path.remove(str(path.parent))


@pytest.fixture(scope="module")
def lib():
    return _load("sleep_lib", "sleep_setup/v1.0.0/_lib.py")


@pytest.fixture(scope="module")
def setup_mod():
    return _load("sleep_setup_mod", "sleep_setup/v1.0.0/sleep_setup.py")


@pytest.fixture(scope="module")
def check_mod():
    return _load("sleep_check_mod", "sleep_check/v1.0.0/sleep_check.py")


@pytest.fixture(scope="module")
def check_mod_v101():
    """sleep_check v1.0.1：完成检测（冒烟发现 ①——服务完成 test_times 后自停）。"""
    return _load("sleep_check_mod_v101", "sleep_check/v1.0.1/sleep_check.py")


@pytest.fixture(scope="module")
def finish_mod():
    return _load("sleep_finish_mod", "sleep_finish/v1.0.0/sleep_finish.py")


@pytest.fixture()
def golden_result() -> bytes:
    return (_FIXTURES / "sleep_test_result.txt").read_bytes()


# ---------------------------------------------------------------------------
# sleep_test_result.txt 解析（纯文本行；join 键 = cycle 分子/分母）
# ---------------------------------------------------------------------------


class TestParseSleepResult:
    def test_golden_summary(self, lib, golden_result):
        parsed = lib.parse_sleep_result(golden_result)
        assert parsed["cycles_done"] == 5
        assert parsed["expected_cycles"] == 100
        assert parsed["wake_failures"] == 1        # cycle 3 wake FAIL
        assert parsed["sleep_anomalies"] == 1      # go sleep ... screen=ON
        assert parsed["final_status"] == "PASS"

    def test_golden_entries(self, lib, golden_result):
        parsed = lib.parse_sleep_result(golden_result)
        kinds = [e["kind"] for e in parsed["entries"]]
        assert kinds.count("cycle") == 5
        assert kinds.count("sleep") == 4        # 5 轮 cycle，4 行 go sleep（末轮未收尾到 sleep）
        assert kinds.count("finished") == 1
        last = parsed["entries"][-1]
        assert last == {"kind": "finished", "result": "PASS"}

    def test_wake_fail_entry_captured(self, lib, golden_result):
        parsed = lib.parse_sleep_result(golden_result)
        failed = [e for e in parsed["entries"] if e["kind"] == "cycle" and e["status"] == "wake FAIL"]
        assert len(failed) == 1
        assert failed[0]["cycle"] == 3
        assert failed[0]["screen"] == "OFF"

    def test_no_timestamp_prefix(self, lib):
        content = (
            "cycle 1/50 wake OK screen=ON\n"
            "go sleep 30s screen=OFF\n"
            "cycle 2/50 wake OK screen=ON\n"
        ).encode()
        parsed = lib.parse_sleep_result(content)
        assert parsed["cycles_done"] == 2
        assert parsed["expected_cycles"] == 50

    def test_crlf_tolerant(self, lib):
        content = b"cycle 1/10 wake OK screen=ON\r\ngo sleep 5s screen=OFF\r\n"
        parsed = lib.parse_sleep_result(content)
        assert parsed["cycles_done"] == 1
        assert parsed["sleep_anomalies"] == 0

    def test_incomplete_no_finished_line(self, lib):
        content = b"cycle 1/100 wake OK screen=ON\ngo sleep 300s screen=OFF\nstopped by user\n"
        parsed = lib.parse_sleep_result(content)
        assert parsed["final_status"] is None
        kinds = [e["kind"] for e in parsed["entries"]]
        assert kinds[-1] == "stopped"

    def test_empty_content(self, lib):
        parsed = lib.parse_sleep_result(b"")
        assert parsed["cycles_done"] == 0
        assert parsed["final_status"] is None
        assert parsed["entries"] == []


# ---------------------------------------------------------------------------
# test-config.properties 解析 + 配置层级（params > env > properties > 默认）
# ---------------------------------------------------------------------------


class TestParseProperties:
    def test_comment_blank_and_values(self, lib):
        cfg = lib.parse_properties("# 注释\n\ntest.times=100\nwake.seconds = 60\n")
        assert cfg == {"test.times": "100", "wake.seconds": "60"}

    def test_empty(self, lib):
        assert lib.parse_properties("") == {}


class TestSleepConfig:
    def _patch_env_props(self, lib, monkeypatch, props: dict, envs: dict):
        monkeypatch.setattr(lib, "read_properties", lambda project: props)
        for key in ("STP_SLEEP_TEST_TIMES", "STP_SLEEP_WAKE_SECONDS", "STP_SLEEP_SLEEP_SECONDS",
                    "STP_SLEEP_TESTER", "STP_SLEEP_AUTO_RESUME", "STP_SLEEP_INSTALL_APKS",
                    "STP_SLEEP_RESET_COUNT", "STP_SLEEP_PROJECT"):
            monkeypatch.delenv(key, raising=False)
        for key, value in envs.items():
            monkeypatch.setenv(key, value)

    def test_code_defaults(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {}, {})
        cfg = lib.sleep_config({})
        assert cfg["test_times"] == 100
        assert cfg["wake_seconds"] == 60
        assert cfg["sleep_seconds"] == 300
        assert cfg["tester"] == "tester"
        assert cfg["auto_resume"] is True
        assert cfg["project"] == "legacy"

    def test_params_win_over_env_and_props(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {"STP_SLEEP_TEST_TIMES": "200"})
        cfg = lib.sleep_config({"test_times": 7})
        assert cfg["test_times"] == 7

    def test_env_over_props(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {"STP_SLEEP_TEST_TIMES": "200"})
        assert lib.sleep_config({})["test_times"] == 200

    def test_props_fallback(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {"test.times": "300"}, {})
        assert lib.sleep_config({})["test_times"] == 300

    def test_boolean_keys(self, lib, monkeypatch):
        self._patch_env_props(lib, monkeypatch, {}, {})
        cfg = lib.sleep_config({"auto_resume": False, "reset_count": "false", "install_apks": True})
        assert cfg["auto_resume"] is False
        assert cfg["reset_count"] is False
        assert cfg["install_apks"] is True

    def test_read_properties_missing_file(self, lib, monkeypatch, tmp_path):
        monkeypatch.delenv("STP_SLEEP_PROJECT", raising=False)
        monkeypatch.setenv("STP_AEE_NFS_ROOT", str(tmp_path))
        assert lib.read_properties("legacy") == {}


# ---------------------------------------------------------------------------
# prefs XML（lib.ps1:Set-SleepTestPrefs / Update-SleepTestPrefsField 同款）
# ---------------------------------------------------------------------------


class TestPrefsXml:
    def test_build_full_map(self, lib):
        xml = lib.build_prefs_xml(100, 60, 300, "tester", True, current_count=7)
        assert 'name="test_times" value="100"' in xml
        assert 'name="current_count" value="7"' in xml
        assert 'name="wake_seconds" value="60"' in xml
        assert 'name="sleep_seconds" value="300"' in xml
        assert 'name="auto_resume" value="true"' in xml
        assert 'name="running" value="false"' in xml
        assert "<string name=\"phase\">idle</string>" in xml
        assert 'name="tester_name">tester<' in xml

    def test_update_field_replace(self, lib):
        xml = lib.build_prefs_xml(100, 60, 300, "tester", True)
        updated = lib.update_prefs_field(xml, "auto_resume", "false", "boolean")
        assert 'name="auto_resume" value="false"' in updated
        assert updated.count("auto_resume") == 1

    def test_update_field_append(self, lib):
        xml = "<?xml version='1.0'?><map></map>"
        updated = lib.update_prefs_field(xml, "running", "true", "boolean")
        assert 'name="running" value="true"' in updated

    def test_unknown_type_raises(self, lib):
        with pytest.raises(ValueError):
            lib.update_prefs_field("<map></map>", "x", "1", "float")

    def test_set_prefs_reset_count_true_zeroes(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "repair_prefs_ownership", lambda: None)
        pushed = {}

        def fake_push(content):
            pushed["xml"] = content

        monkeypatch.setattr(lib, "push_prefs_xml", fake_push)
        monkeypatch.setattr(lib, "get_prefs_xml", lambda: "")
        cfg = {"test_times": 100, "wake_seconds": 60, "sleep_seconds": 300,
               "tester": "tester", "auto_resume": True, "reset_count": True}
        assert lib.set_prefs(cfg) == 0
        assert 'name="current_count" value="0"' in pushed["xml"]

    def test_set_prefs_reset_count_false_preserves(self, lib, monkeypatch):
        monkeypatch.setattr(lib, "repair_prefs_ownership", lambda: None)
        pushed = {}

        def fake_push(content):
            pushed["xml"] = content

        monkeypatch.setattr(lib, "push_prefs_xml", fake_push)
        monkeypatch.setattr(
            lib, "get_prefs_xml",
            lambda: '<?xml version="1.0"?><map><int name="current_count" value="42"/></map>',
        )
        cfg = {"test_times": 100, "wake_seconds": 60, "sleep_seconds": 300,
               "tester": "tester", "auto_resume": True, "reset_count": False}
        assert lib.set_prefs(cfg) == 42
        assert 'name="current_count" value="42"' in pushed["xml"]


# ---------------------------------------------------------------------------
# sleep_setup：fail-fast（APK 缺失）
# ---------------------------------------------------------------------------


class TestSetupFailFast:
    def test_missing_apk_raises(self, setup_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(setup_mod, "sleep_config", lambda cfg: {"project": "legacy"})
        monkeypatch.setattr(setup_mod, "resources_dir", lambda cfg: tmp_path)
        with pytest.raises(FileNotFoundError) as ei:
            setup_mod._run({})
        assert "AutoTestTool.apk" in str(ei.value)


# ---------------------------------------------------------------------------
# sleep_check：进度/存活逻辑（monkeypatch ADB）
# ---------------------------------------------------------------------------


class TestCheckProgress:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, alive=True, prefs=(3, 100),
                         result_bytes=2048):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "service_alive", lambda: alive)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: prefs)
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: result_bytes)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_prefs_progress_used(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({})
        assert r["success"] is True
        assert r["progress"]["cycles_done"] == 3
        assert r["progress"]["expected_cycles"] == 100

    def test_injected_expected_wins_over_prefs(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path)
        r = check_mod._run({"expected_cycles": 130})
        assert r["progress"]["expected_cycles"] == 130

    def test_prefs_unavailable_falls_back_to_grep(self, check_mod, monkeypatch, tmp_path):
        self._patch_device_io(check_mod, monkeypatch, tmp_path, prefs=None)
        monkeypatch.setattr(check_mod, "_grep_cycle_count", lambda: 9)
        r = check_mod._run({})
        assert r["progress"]["cycles_done"] == 9
        assert r["progress"]["expected_cycles"] == 0    # prefs 缺失 → 只报绝对数

    def test_dead_streak_grace(self, check_mod, monkeypatch, tmp_path):
        """连续 2 周期未存活才判死；第 1 周期仍 success。"""
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S2")
        monkeypatch.setattr(check_mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(check_mod, "service_alive", lambda: False)
        monkeypatch.setattr(check_mod, "_read_prefs_progress", lambda: (1, 100))
        monkeypatch.setattr(check_mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(check_mod, "_result_bytes", lambda: 100)
        monkeypatch.setattr(check_mod, "progress_stamp", lambda payload: None)

        r1 = check_mod._run({"dead_grace_cycles": 2})
        assert r1["success"] is True
        assert r1["progress"]["service_alive"] is False
        r2 = check_mod._run({"dead_grace_cycles": 2})
        assert r2["success"] is False
        assert "连续 2 个周期" in r2["error_message"]

    def test_alive_resets_streak(self, check_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(check_mod, "device_serial", lambda: "S3")
        monkeypatch.setattr(check_mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(check_mod, "_read_prefs_progress", lambda: (1, 100))
        monkeypatch.setattr(check_mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(check_mod, "_result_bytes", lambda: 100)
        monkeypatch.setattr(check_mod, "progress_stamp", lambda payload: None)
        alive = {"v": False}
        monkeypatch.setattr(check_mod, "service_alive", lambda: alive["v"])
        check_mod._run({})
        alive["v"] = True
        r2 = check_mod._run({})
        assert r2["success"] is True
        assert r2["progress"]["seq"] == 2


# ---------------------------------------------------------------------------
# sleep_finish：拉取 + 解析 + 落盘
# ---------------------------------------------------------------------------


class TestFinish:
    def test_pull_primary_path(self, finish_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(
            finish_mod, "result_paths",
            lambda: ("/sdcard/Android/data/com.tinno.autotesttool/files/SleepTest/sleep_test_result.txt",),
        )
        monkeypatch.setattr(
            finish_mod, "adb_shell",
            lambda cmd, timeout=30: "-rw-rw---- root sdcard_rw 512 2026-08-31 01:00 "
            f"{finish_mod.result_paths()[0]}",
        )
        calls = {}

        def fake_adb(*args, timeout=60):
            if args[0] == "pull":
                Path(args[2]).write_bytes(b"cycle 1/10 wake OK screen=ON\n")
                calls["pulled"] = True
            return (0, "", "")

        monkeypatch.setattr(finish_mod, "adb", fake_adb)
        local = finish_mod._pull_result_file()
        assert local.is_file()
        assert calls.get("pulled")

    def test_pull_missing_raises(self, finish_mod, monkeypatch):
        monkeypatch.setattr(finish_mod, "result_paths", lambda: ("/sdcard/x/sleep_test_result.txt",))
        monkeypatch.setattr(
            finish_mod, "adb_shell",
            lambda cmd, timeout=30: "No such file or directory",
        )
        with pytest.raises(RuntimeError) as ei:
            finish_mod._pull_result_file()
        assert "sleep_test_result.txt" in str(ei.value)

    def test_run_writes_detail_json(self, finish_mod, monkeypatch, tmp_path):
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "SLEEP-S1")
        monkeypatch.setattr(finish_mod, "stop_task", lambda force=True: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)
        monkeypatch.setattr(finish_mod, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "sleep_test_result.txt"
            local.write_bytes(
                b"cycle 1/10 wake OK screen=ON\ngo sleep 5s screen=OFF\nfinished result=PASS\n"
            )
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_file", fake_pull)
        results = tmp_path / "nfs" / "sleep" / "legacy" / "results"
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: results)

        out = finish_mod._run({"project": "legacy"})
        assert out["metrics"]["cycles_done"] == 1
        assert out["metrics"]["expected_cycles"] == 10
        assert out["metrics"]["final_status"] == "PASS"
        detail = results / f"{out['metrics']['run_id']}.json"
        assert detail.is_file()
        body = json.loads(detail.read_text(encoding="utf-8"))
        assert body["metrics"]["final_status"] == "PASS"
        assert body["entries"][-1]["kind"] == "finished"

    def test_run_incomplete_marked(self, finish_mod, monkeypatch, tmp_path):
        """无 finished 行 → final_status=INCOMPLETE（测试未收尾）。"""
        monkeypatch.setattr(finish_mod, "device_serial", lambda: "SLEEP-S2")
        monkeypatch.setattr(finish_mod, "stop_task", lambda force=True: None)
        monkeypatch.setattr(finish_mod.time, "sleep", lambda _: None)
        monkeypatch.setattr(finish_mod, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "sleep_test_result.txt"
            local.write_bytes(b"cycle 1/10 wake OK screen=ON\nstopped by user\n")
            return local

        monkeypatch.setattr(finish_mod, "_pull_result_file", fake_pull)
        monkeypatch.setattr(finish_mod, "results_dir", lambda project: tmp_path / "r")
        out = finish_mod._run({})
        assert out["metrics"]["final_status"] == "INCOMPLETE"


# ---------------------------------------------------------------------------
# sleep_check v1.0.1 完成检测（冒烟发现 ①：服务完成 test_times 后自停）
# ---------------------------------------------------------------------------


class TestCheckV101Completion:
    def _patch_device_io(self, mod, monkeypatch, tmp_path, finished=True, alive=False, prefs=(2, 2)):
        monkeypatch.setattr(mod, "device_serial", lambda: "S1")
        monkeypatch.setattr(mod, "_state_file", lambda: tmp_path / "state.json")
        monkeypatch.setattr(mod, "service_alive", lambda: alive)
        monkeypatch.setattr(mod, "_read_prefs_progress", lambda: prefs)
        monkeypatch.setattr(mod, "_grep_cycle_count", lambda: 0)
        monkeypatch.setattr(mod, "_result_bytes", lambda: 383)
        monkeypatch.setattr(mod, "_run_finished", lambda: finished)
        monkeypatch.setattr(mod, "progress_stamp", lambda payload: None)

    def test_finished_reports_completion_not_death(self, check_mod_v101, monkeypatch, tmp_path):
        """服务自停 + 结果文件 finished → 报完成而非判死（真机冒烟实测行为）。"""
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=True, alive=False)
        r = check_mod_v101._run({})
        assert r["success"] is True
        assert r["progress"]["run_finished"] is True
        assert r["progress"]["cycles_done"] == 2
        assert r["progress"]["service_alive"] is False

    def test_finished_never_accumulates_dead_streak(self, check_mod_v101, monkeypatch, tmp_path):
        """连续多周期 finished 全部 success（v1.0.0 会在第 2 周期判死）。"""
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=True, alive=False)
        for _ in range(5):
            r = check_mod_v101._run({"dead_grace_cycles": 2})
            assert r["success"] is True

    def test_not_finished_keeps_v100_dead_streak(self, check_mod_v101, monkeypatch, tmp_path):
        """无 finished 行时 v1.0.0 判死语义保留（中途崩溃仍会失败）。"""
        self._patch_device_io(check_mod_v101, monkeypatch, tmp_path, finished=False, alive=False)
        r1 = check_mod_v101._run({"dead_grace_cycles": 2})
        assert r1["success"] is True
        r2 = check_mod_v101._run({"dead_grace_cycles": 2})
        assert r2["success"] is False
        assert "连续 2 个周期" in r2["error_message"]

    def test_run_finished_detects_marker(self, check_mod_v101, monkeypatch):
        monkeypatch.setattr(check_mod_v101, "device_serial", lambda: "S1")
        monkeypatch.setattr(check_mod_v101, "result_paths", lambda: ("/sdcard/x/sleep_test_result.txt",))

        class FakeResult:
            stdout = "2026-08-07 20:52:53 cycle 2/2 wake OK screen=ON\nfinished result=PASS\n"

        monkeypatch.setattr(check_mod_v101.subprocess, "run", lambda *a, **k: FakeResult())
        assert check_mod_v101._run_finished() is True

    def test_run_finished_no_marker(self, check_mod_v101, monkeypatch):
        monkeypatch.setattr(check_mod_v101, "device_serial", lambda: "S1")
        monkeypatch.setattr(check_mod_v101, "result_paths", lambda: ("/sdcard/x/sleep_test_result.txt",))

        class FakeResult:
            stdout = "cycle 1/2 wake OK screen=ON\n"

        monkeypatch.setattr(check_mod_v101.subprocess, "run", lambda *a, **k: FakeResult())
        assert check_mod_v101._run_finished() is False


# ---------------------------------------------------------------------------
# sleep_finish v1.0.1：run_id 设备维度（验收发现⑨）
# ---------------------------------------------------------------------------


class TestFinishV101RunId:
    def test_run_id_has_serial(self, monkeypatch, tmp_path):
        mod = _load("sleep_finish_mod_v101", "sleep_finish/v1.0.1/sleep_finish.py")
        monkeypatch.setattr(mod, "device_serial", lambda: "SLEEP-S9")
        monkeypatch.setattr(mod, "stop_task", lambda force=True: None)
        monkeypatch.setattr(mod.time, "sleep", lambda _: None)
        monkeypatch.setattr(mod, "adb_shell", lambda cmd, timeout=30: "")

        def fake_pull():
            local = tmp_path / "sleep_test_result.txt"
            local.write_bytes(b"cycle 1/2 wake OK screen=ON\n")
            return local

        monkeypatch.setattr(mod, "_pull_result_file", fake_pull)
        monkeypatch.setattr(mod, "results_dir", lambda project: tmp_path / "r")
        out = mod._run({})
        assert out["metrics"]["run_id"].endswith("_SLEEP-S9")


# ---------------------------------------------------------------------------
# PROGRESS 打戳格式（#115 契约）
# ---------------------------------------------------------------------------


class TestProgressStamp:
    def test_stderr_line(self, lib, capsys):
        lib.progress_stamp({"seq": 1, "step": "sleep_check"})
        captured = capsys.readouterr()
        assert captured.err.strip() == 'PROGRESS {"seq": 1, "step": "sleep_check"}'
        assert captured.out == ""
