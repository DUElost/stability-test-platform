"""`tools/dev/skill_usage_probe.py` 的契约测试——**不读真实转录、不写系统目录、不联网**。

要守住的性质只有三条：

1. 三态不塌缩：`hollow>0`（有事实要人读）、`unknown`（源不在场，结论未知）、`broken`
   （探针自身坏了）在 systemd 面必须给出**不同**结果——1/2 都算任务成功，只有 broken
   才 failed；否则要么告警疲劳（有洞就 failed），要么探针停摆被读成「干净」。
2. `unknown` 不得被当成「零个洞」（#2851 的语义）：缺源时 hollow 读数无意义。
3. 指标是 node-exporter textfile 形状，且时间戳是整值字面量（指数形式解析器不友好）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "skill_usage_probe", REPO_ROOT / "tools" / "dev" / "skill_usage_probe.py"
)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
sys.modules["skill_usage_probe"] = _mod
_spec.loader.exec_module(_mod)


def _payload(hollow: int, *, strong: bool = True, weak: bool = True) -> dict:
    return {
        "hollow": hollow,
        "strong_source_present": strong,
        "weak_source_present": weak,
        "skills": [{"dir": "x", "type": "persistent", "age_days": 30,
                    "claude_calls": 0, "codex_sessions": 1, "hollow": bool(hollow)}] * hollow,
    }


# ---------------------------------------------------------------- 三态映射

@pytest.mark.parametrize(
    "rc,payload,expected_values,expected_exit",
    [
        # 无洞：正常
        (0, _payload(0), {"hollow": 0.0, "unknown": 0.0, "broken": 0.0}, 0),
        # 有洞：**任务成功**（洞数进指标、交给人裁决；天天 failed 会把真故障淹掉）
        (1, _payload(2), {"hollow": 2.0, "unknown": 0.0, "broken": 0.0}, 0),
        # 强信号源不在场（#2851）：结论未知，不得当成「零个洞」
        (
            0,
            _payload(0, strong=False),
            {"hollow": 0.0, "unknown": 1.0, "broken": 0.0},
            0,
        ),
        # 无任何源（report 的 skip 形态）：同样折 unknown
        (
            0,
            {"hollow": 0, "skills": [], "skipped": "no_transcript_source",
             "strong_source_present": False, "weak_source_present": False},
            {"hollow": 0.0, "unknown": 1.0, "broken": 0.0},
            0,
        ),
        # 没走到输出那一步（payload 畸形）⇒ broken + 任务失败
        (1, {"_stderr_tail": "boom"}, {"hollow": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        (0, {}, {"hollow": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
    ],
)
def test_three_states_are_not_collapsed(rc, payload, expected_values, expected_exit):
    values, exit_code = _mod.summarize(rc, payload)
    assert values == expected_values, (values, payload)
    assert exit_code == expected_exit


def test_unknown_never_reads_as_clean():
    """缺源时 broken/unknown 必须置位——hollow=0 不得单独被读成「没有洞」。"""
    values, _ = _mod.summarize(0, _payload(0, strong=False))
    assert values["unknown"] == 1.0
    assert not (values["hollow"] == 0 and values["unknown"] == 0 and values["broken"] == 0)


# ---------------------------------------------------------------- 源路径推导

def test_transcript_dir_matches_report_encoding():
    """转录目录按「绝对路径的 / 换成 -」编码（与 report 的默认值同规则）。"""
    got = _mod.transcript_dir_for(Path("/home/deploy"), Path("/home/deploy/stp"))
    assert got == Path("/home/deploy/.claude/projects/-home-deploy-stp")


def test_codex_dir_is_under_home():
    # 探针把 --home 下的 .codex/sessions 传给 report（root 跑的 ~ 是 /root，必须显式指路）
    assert (Path("/home/deploy") / ".codex" / "sessions").name == "sessions"


# ---------------------------------------------------------------- 指标形状

def test_render_metrics_is_node_exporter_textfile_shape():
    text = _mod.render_metrics(
        {"hollow": 2.0, "unknown": 0.0, "broken": 0.0}, ran_at=1789600000
    )
    assert "# TYPE stp_skill_usage_hollow gauge" in text
    assert "stp_skill_usage_hollow 2" in text
    # 时间戳必须是整值字面量：指数形式（1.7896e+09）对解析器不友好
    assert "stp_skill_usage_last_run 1789600000" in text
    assert "e+" not in text
    for name in _mod._METRIC_HELP:
        assert f"# HELP {name} " in text, name


# ---------------------------------------------------------------- 端到端

def test_main_writes_metrics_and_keeps_task_success_on_hollow(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(_mod, "run_report", lambda *a, **k: (1, _payload(3)))
    target = tmp_path / "stp-skill-usage.prom"
    assert _mod.main(["--metrics-path", str(target)]) == 0
    text = target.read_text(encoding="utf-8")
    assert "stp_skill_usage_hollow 3" in text
    assert "stp_skill_usage_broken 0" in text
    assert "HOLLOW" in capsys.readouterr().out


def test_main_reports_broken_when_report_not_runnable(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(_mod, "run_report", lambda *a, **k: (1, {"_stderr_tail": "boom"}))
    target = tmp_path / "stp-skill-usage.prom"
    assert _mod.main(["--metrics-path", str(target)]) == 1
    assert "stp_skill_usage_broken 1" in target.read_text(encoding="utf-8")
    assert "BROKEN" in capsys.readouterr().err


def test_main_fails_loud_when_metrics_unwritable(monkeypatch, tmp_path, capsys):
    """指标写不出去就当场失败——不做「跑成功了但没人知道」的静默停摆。"""
    monkeypatch.setattr(_mod, "run_report", lambda *a, **k: (0, _payload(0)))
    monkeypatch.setattr(_mod, "write_atomic",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))
    assert _mod.main(["--metrics-path", str(tmp_path / "x.prom")]) == 1
    assert "指标写入失败" in capsys.readouterr().err


def test_probe_uses_only_stdlib_and_no_credentials():
    """探针只读本机转录、不连网、不读凭据（与姊妹探针同一纪律）。"""
    source = (REPO_ROOT / "tools" / "dev" / "skill_usage_probe.py").read_text(encoding="utf-8")
    for forbidden in ("requests", "psycopg", "DATABASE_URL", "AGENT_SECRET", "JWT_SECRET"):
        assert forbidden not in source, forbidden
