"""#2975：monkey_setup **v2.3.10**——结构化出口必须落在引擎读取的**顶层** metrics。

缺陷（v2.3.9/#2862 的未生效半程）：`att_stop_issued` / `att_prefs_cleared` 落在
**步骤字典**里（`steps.att_clean.metrics`），而 `pipeline_engine._run_script_action`
只读顶层 `payload.get("metrics", {})` ⇒ 恒为 `{}`，「降级结论进观测面」实际零消费者，
`att_warnings` 的静默命运原样重演（本文件的锚点会钉住这一形状）。

v2.3.10 锁定：
1. `main()` 成功与失败两条出口都带顶层 `metrics`（各步骤 metrics 聚合）；
2. 步骤内嵌位置**不变**（step_trace 复盘兼容）；
3. 引擎 e2e：把脚本产出的 stdout 原样喂给 `PipelineEngine`，断言顶层 metrics
   进到 `StepResult`/`_shared` 读取面——补上 #2862 缺的那条端到端用例。
对照锚点：同步骤桩下 v2.3.9 的 stdout 顶层**无** `metrics` 键。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "agent" / "scripts" / "monkey_setup"

_ATT_METRICS = {"att_stop_issued": 0, "att_prefs_cleared": 1}


def _load_version(version: str, tag: str):
    d = _SCRIPTS / version
    spec = importlib.util.spec_from_file_location(f"_adb_{tag}", d / "_adb.py")
    assert spec and spec.loader
    adb_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adb_mod)
    sys.modules["_adb"] = adb_mod
    spec2 = importlib.util.spec_from_file_location(f"monkey_setup_{tag}", d / "monkey_setup.py")
    assert spec2 and spec2.loader
    mod = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(mod)
    sys.modules.pop("_adb", None)
    return mod


def _run_main(monkeypatch, mod, *, steps_result: dict, params: dict):
    def fake_att(serial, cfg):
        return dict(steps_result)

    monkeypatch.setitem(mod.STEPS, "att_clean", fake_att)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.setenv("STP_DEVICE_SERIAL", "TESTSERIAL")
    monkeypatch.setenv("STP_STEP_PARAMS", json.dumps({"steps": ["att_clean"], **params}))
    mod.main()  # 成功路径正常返回；失败路径自行用 pytest.raises(SystemExit) 包住


def _payload(capsys) -> dict:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "main() 未产出 stdout JSON"
    return json.loads(out[-1])


class TestV2310TopLevelMetrics:
    def test_success_exit_carries_top_level_metrics(self, monkeypatch, capsys):
        mod = _load_version("v2.3.10", "ms_v2310")
        _run_main(
            monkeypatch, mod,
            steps_result={"success": True, "att_prefs_cleared": True, "metrics": _ATT_METRICS},
            params={},
        )
        payload = _payload(capsys)
        assert payload["success"] is True
        assert payload["metrics"] == _ATT_METRICS, "顶层 metrics 缺失——#2975 修复未生效"
        # 步骤内嵌位置不变（复盘面兼容）
        assert payload["steps"]["att_clean"]["metrics"] == _ATT_METRICS

    def test_failure_exit_also_carries_top_level_metrics(self, monkeypatch, capsys):
        """att_clean 自身失败时降级结论同样不得丢（失败出口是更常见的那条）。"""
        mod = _load_version("v2.3.10", "ms_v2310")
        with pytest.raises(SystemExit) as boom:
            _run_main(
                monkeypatch, mod,
                steps_result={"success": False, "error": "rm prefs rc=1", "metrics": _ATT_METRICS},
                params={},
            )
        assert boom.value.code == 1
        payload = _payload(capsys)
        assert payload["success"] is False
        assert payload["metrics"] == _ATT_METRICS

    def test_steps_without_metrics_yield_empty_dict(self, monkeypatch, capsys):
        """无 metrics 步骤不炸出口形状：顶层键恒存在。"""
        mod = _load_version("v2.3.10", "ms_v2310")
        _run_main(monkeypatch, mod, steps_result={"success": True}, params={})
        payload = _payload(capsys)
        assert payload["metrics"] == {}


class TestEngineReadsTopLevel:
    """AC 的端到端用例：脚本产出的 stdout 原样过 pipeline_engine，metrics 上进 StepResult 读取面。"""

    def test_engine_step_result_metrics_contains_att_keys(self, tmp_path):
        from backend.agent.pipeline_engine import PipelineEngine

        captured = {
            "success": True,
            "steps": {"att_clean": {"success": True, "metrics": _ATT_METRICS}},
            "total_duration_s": 0.0,
            "metrics": _ATT_METRICS,
        }
        script = tmp_path / "monkey_setup_e2e.py"
        script.write_text(f"import sys\nsys.stdout.write({json.dumps(json.dumps(captured))} + '\\n')\n")

        class FakeRegistry:
            def resolve(self, name, version):
                return SimpleNamespace(
                    script_id=1, name=name, version=version, script_type="python",
                    nfs_path=str(script), content_sha256="c" * 64,
                )

        engine = PipelineEngine(
            adb=SimpleNamespace(adb_path="adb"), serial="SERIAL001", run_id=1,
            script_registry=FakeRegistry(),
        )
        result = engine.execute({"lifecycle": {
            "init": [{"step_id": "att_setup", "action": "script:monkey_setup",
                      "version": "2.3.10", "params": {}, "timeout_seconds": 10}],
            "teardown": [],
        }})
        assert result.success is True
        assert engine._shared["att_setup"] == _ATT_METRICS, (
            "引擎从顶层 metrics 读到的不是 att 降级结论（StepResult.metrics 面失配）"
        )


class TestV239Anchor:
    def test_anchor_v239_stdout_has_no_top_level_metrics(self, monkeypatch, capsys):
        """对照锚点：v2.3.9 同桩下顶层无 metrics 键——零消费者形态确由 v2.3.10 消除。"""
        mod = _load_version("v2.3.9", "ms_v239_anchor")
        _run_main(
            monkeypatch, mod,
            steps_result={"success": True, "att_prefs_cleared": True, "metrics": _ATT_METRICS},
            params={},
        )
        payload = _payload(capsys)
        assert "metrics" not in payload, "锚点失效：v2.3.9 顶层已带 metrics（前提需重审）"
        assert payload["steps"]["att_clean"]["metrics"] == _ATT_METRICS
