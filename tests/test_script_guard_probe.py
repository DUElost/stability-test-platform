"""`tools/dev/script_guard_probe.py` 的契约测试——**不连库、不联网、不需要凭据**。

要守住的性质只有一条：`--guard` 的四个码不能被压成"任务成功/失败"两态。
1（有到期项）与 3（工具坏了）在 systemd 面必须是**不同**结果，否则要么告警疲劳
（1 也 failed），要么守卫静默停摆（3 也 ok）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "script_guard_probe", REPO_ROOT / "tools" / "dev" / "script_guard_probe.py")
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["script_guard_probe"] = _mod
_spec.loader.exec_module(_mod)


def _guard_payload(status: str, violations: int) -> dict:
    return {"guard": {"status": status, "violations": violations},
            "retirement_plan": [{"name": "s", "version": "1.0.0"}] * violations}


# ---------------------------------------------------------------- 三态映射

@pytest.mark.parametrize(
    "rc,payload,expected_values,expected_exit",
    [
        # 无到期项：正常，指标 due=0
        (0, _guard_payload("OK", 0), {"due": 0.0, "unknown": 0.0, "broken": 0.0}, 0),
        # 有到期项：**任务成功**（否则 timer 天天 failed，真故障被淹掉），数量进指标
        (1, _guard_payload("FAIL", 3), {"due": 3.0, "unknown": 0.0, "broken": 0.0}, 0),
        # 码说判红但 payload 给不出数量：按 1 报，绝不显示"干净"
        (1, {}, {"due": 1.0, "unknown": 0.0, "broken": 0.0}, 0),
        # 使用事实不可得：显式 unknown，不降级成 due=0
        (2, _guard_payload("UNKNOWN", 0), {"due": 0.0, "unknown": 1.0, "broken": 0.0}, 0),
        # 工具自身异常：唯一要让 systemd 标 failed 的一档
        (3, {}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
    ],
)
def test_guard_codes_are_not_collapsed_into_task_status(rc, payload, expected_values,
                                                        expected_exit):
    values, exit_code = _mod.summarize(rc, payload)
    assert values == expected_values
    assert exit_code == expected_exit


def test_due_and_broken_are_distinguishable_in_metrics():
    """`due=0` 不能同时表示"干净"和"工具坏了"——两档必须落到不同指标。"""
    ok, _ = _mod.summarize(0, _guard_payload("OK", 0))
    broken, _ = _mod.summarize(3, {})
    assert ok["broken"] == 0.0 and broken["broken"] == 1.0
    assert broken["due"] == 0.0  # 坏了的时候不得报"有 N 条待退役"


# ---------------------------------------------------------------- 指标渲染/落盘

def test_render_metrics_is_node_exporter_textfile_shape():
    text = _mod.render_metrics({"due": 2.0, "unknown": 0.0, "broken": 0.0}, ran_at=1789600000)
    assert "stp_script_guard_due 2" in text
    assert "# TYPE stp_script_guard_due gauge" in text
    assert "stp_script_guard_last_run 1789600000" in text
    for line in text.splitlines():
        assert not line.endswith(" ")
    assert text.endswith("\n")


def test_write_metrics_replaces_atomically(tmp_path):
    target = tmp_path / "nested" / "stp-script-guard.prom"
    _mod.write_metrics(target, "old\n")
    _mod.write_metrics(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert not list(tmp_path.rglob("*.tmp"))  # 不留半截文件


# ---------------------------------------------------------------- main 端到端

@pytest.mark.parametrize(
    "rc,expected_exit,expected_in_file",
    [
        (0, 0, "stp_script_guard_due 0"),
        (1, 0, "stp_script_guard_due 4"),
        (2, 0, "stp_script_guard_unknown 1"),
        (3, 1, "stp_script_guard_broken 1"),
    ],
)
def test_main_writes_metrics_and_maps_task_exit(monkeypatch, tmp_path, rc, expected_exit,
                                                expected_in_file):
    payload = _guard_payload({0: "OK", 1: "FAIL", 2: "UNKNOWN"}.get(rc, "OK"),
                             4 if rc == 1 else 0)
    monkeypatch.setattr(_mod, "run_guard", lambda exe, today: (rc, payload))
    metrics = tmp_path / "guard.prom"
    assert _mod.main(["--metrics-path", str(metrics), "--today", "2026-09-17"]) == expected_exit
    text = metrics.read_text(encoding="utf-8")
    assert expected_in_file in text
    assert "stp_script_guard_last_run" in text  # 跑过就必须可证，防静默停摆


def test_main_fails_loud_when_metrics_unwritable(monkeypatch, tmp_path, capsys):
    """指标写不出去 = 守卫等于没跑，必须当场失败而不是"跑成功了但没人知道"。"""
    monkeypatch.setattr(_mod, "run_guard", lambda exe, today: (0, _guard_payload("OK", 0)))
    blocked = tmp_path / "blocked"
    blocked.write_text("i am a file", encoding="utf-8")  # 父级不是目录 ⇒ mkdir 必失败
    assert _mod.main(["--metrics-path", str(blocked / "guard.prom")]) == 1
    assert "指标文件写入失败" in capsys.readouterr().err


def test_unrunnable_guard_still_reports_broken(tmp_path):
    """E2E：判据根本起不来（解释器不存在）时，必须**照样写出 broken 指标**。

    真跑 subprocess，不打桩——`ControlPlane` 被 FakeClient 替掉导致登录缺陷漏网
    （同日生产实跑 401）就是反例：把被测路径替掉的测试等于没测。
    旧实现在这里抛 FileNotFoundError、指标不更新 ⇒ 消费方读到陈旧的 due=0。
    """
    metrics = tmp_path / "guard.prom"
    rc = _mod.main(["--python", str(tmp_path / "no-such-python"),
                    "--metrics-path", str(metrics)])
    assert rc == 1, "工具起不来必须让 timer 失败"
    text = metrics.read_text(encoding="utf-8")
    assert "stp_script_guard_broken 1" in text
    assert "stp_script_guard_due 0" in text  # 坏了不得报「无到期项」的数值语义
    assert "stp_script_guard_last_run" in text  # 这次执行本身要可证


def test_probe_uses_only_stdlib_and_no_credentials():
    """静态自证：执行者不 import backend、不读凭据——它只 subprocess 调判据模块。

    它跑在生产控制面上，任何"顺手 import 一下 backend"都会把 import 期解析
    DATABASE_URL 的副作用带进来（#735 §1.3 的同一形态）。
    """
    src = (_mod.REPO_ROOT / "tools" / "dev" / "script_guard_probe.py").read_text(encoding="utf-8")
    assert "from backend" not in src and "import backend" not in src
    assert "STP_ADMIN" not in src and "AGENT_SECRET" not in src
    assert "--guard" in src  # 判定确实来自契约端点，不是自己另写一套口径
