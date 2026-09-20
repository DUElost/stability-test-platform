"""`tools/dev/script_guard_probe.py` 的契约测试——**不连库、不联网、不需要凭据**。

要守住的性质只有一条：`--guard` 的四个码不能被压成"任务成功/失败"两态。
1（有到期项）与 3（工具坏了）在 systemd 面必须是**不同**结果，否则要么告警疲劳
（1 也 failed），要么守卫静默停摆（3 也 ok）。
"""
from __future__ import annotations

import importlib.util
import subprocess
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
        # #2797：rc=1 但不带 `guard` 块 ⇒ 判据 import 期就炸（解释器默认码同为 1），
        # 折成 broken 而不是「有活要干」——否则守卫的死讯被读成 DUE、broken 永不置位
        (1, {}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        (1, {"_stderr_tail": "RuntimeError: DATABASE_URL 未配置"},
         {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        # 使用事实不可得：显式 unknown，不降级成 due=0
        (2, _guard_payload("UNKNOWN", 0), {"due": 0.0, "unknown": 1.0, "broken": 0.0}, 0),
        # #2884：rc=2 也可能是 argparse 用法错误（与 UNKNOWN 同码）——没有 `guard`
        # 块就是「进程没走到输出那一步」，按 broken 归因而不是「未知」
        (2, {"_stderr_tail": "argparse: unrecognized arguments: --guard"},
         {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        # #2884：rc=0 的 payload 形状漂移（判据多打一行 / 输出被 banner 污染）——
        # 原先直接落成 "GUARD OK: 无到期项"，broken 恒 0
        (0, {"_stdout_tail": "garbage"}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        (0, {}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        # `guard` 块在但形状不对：同属漂移，不得 AttributeError 崩掉巡检（崩 = 指标停在旧值）
        (0, {"guard": ["not-a-dict"]}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        (0, {"guard": {"violations": "abc"}}, {"due": 0.0, "unknown": 0.0, "broken": 1.0}, 1),
        # 码说有到期项而数量取不出：显示 1（现有口径），不因形状漂移把脏读成干净
        (1, {"guard": {"violations": "abc"}}, {"due": 1.0, "unknown": 0.0, "broken": 0.0}, 0),
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


def test_import_time_death_is_broken_not_due():
    """#2797：rc=1 的两种来源必须可区分——真判定（带 `guard` 块）vs 进程早死（没有）。

    来源：`backend/scripts/check_unreferenced_script_versions.py` 的 module 级
    `resolve_database_url()` / `create_engine` 位于 `main()` 的 try **之前**；环境缺
    DATABASE_URL 时进程在打印 payload 之前退出，解释器默认退出码 1 与 `--guard` 的
    DUE 同码——只看退出码会把守卫的死讯读成「有活要干」。
    """
    died, died_exit = _mod.summarize(1, {"_stderr_tail": "RuntimeError: DATABASE_URL"})
    real_due, due_exit = _mod.summarize(1, _guard_payload("FAIL", 2))
    assert died == {"due": 0.0, "unknown": 0.0, "broken": 1.0} and died_exit == 1
    assert real_due == {"due": 2.0, "unknown": 0.0, "broken": 0.0} and due_exit == 0


def test_main_reports_import_time_death_as_broken(monkeypatch, tmp_path, capsys):
    """端到端：判据早死时指标写 broken=1、任务 exit 1、stderr 带判据侧线索。"""
    monkeypatch.setattr(
        _mod, "run_guard",
        lambda exe, today: (1, {"_stderr_tail": "RuntimeError: DATABASE_URL 未配置"}),
    )
    metrics = tmp_path / "guard.prom"
    assert _mod.main(["--metrics-path", str(metrics)]) == 1
    text = metrics.read_text(encoding="utf-8")
    assert "stp_script_guard_broken 1" in text
    assert "stp_script_guard_due 0" in text
    assert "GUARD BROKEN" in capsys.readouterr().err


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

# ---------------------------------------------------------------- 判据来源归因

MAIN_SHA = "c" * 40


def _fake_git(sha=MAIN_SHA, branch=None, has_origin=True, not_git=False):
    def run(cmd, *a, **k):
        if not_git:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="not a git repo")
        joined = " ".join(cmd)
        if "--format=%h" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=sha[:7], stderr="")
        if "rev-parse HEAD" in joined:
            return subprocess.CompletedProcess(cmd, 0, stdout=sha, stderr="")
        if "symbolic-ref" in joined:
            if branch is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="",
                                                   stderr="not a symbolic ref")
            return subprocess.CompletedProcess(cmd, 0, stdout=branch, stderr="")
        if "rev-parse origin/main" in joined:
            if not has_origin:
                return subprocess.CompletedProcess(cmd, 128, stdout="",
                                                   stderr="unknown revision")
            return subprocess.CompletedProcess(cmd, 0, stdout=MAIN_SHA, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected " + joined)

    return run


@pytest.mark.parametrize(
    "sha,branch,has_origin,expect_trust,expect_in",
    [
        (MAIN_SHA, "main", True, True, "== origin/main"),
        # 特性分支但内容与 main 一致 ⇒ 可信（按分支名判会误报）
        (MAIN_SHA, "fix/some", True, True, "== origin/main"),
        (MAIN_SHA, None, True, True, "== origin/main"),
        # 内容不是 main ⇒ 不可信，并把两个短码都打出来便于归因
        ("a" * 40, "fix/other", True, False, "≠ origin/main"),
        (MAIN_SHA, "main", False, False, "无 origin/main 引用"),
    ],
    ids=["main", "branch-same-sha", "detached-same-sha", "differs", "no-origin"],
)
def test_guard_source_trust_is_decided_by_content(monkeypatch, tmp_path, sha, branch,
                                                  has_origin, expect_trust, expect_in):
    """巡检用的判据代码取自**主检出的当前内容**，所以必须能自证它是不是 origin/main。

    别家会话把检出切走并改动 `script_retirement.py` 后，每日巡检会静默采用它并给出一个
    看起来正常的 due 数（ADR-0046 的「部署源 vs 开发工作区」蔓延到只读巡检）。
    """
    monkeypatch.setattr(_mod.subprocess, "run", _fake_git(sha, branch, has_origin))
    desc, trust = _mod.describe_guard_source(tmp_path)
    assert trust is expect_trust, desc
    assert expect_in in desc, desc


def test_guard_source_non_git_root_is_reported(monkeypatch, tmp_path):
    monkeypatch.setattr(_mod.subprocess, "run", _fake_git(not_git=True))
    desc, trust = _mod.describe_guard_source(tmp_path)
    assert trust is False and "非 git 树" in desc


def test_main_logs_guard_source_without_changing_verdict(monkeypatch, tmp_path, capsys):
    """归因只加一行日志：判定码与指标必须与之前完全一致（不制造新的告警面）。"""
    monkeypatch.setattr(_mod, "run_guard",
                        lambda exe, today: (0, _guard_payload("OK", 0)))
    monkeypatch.setattr(_mod.subprocess, "run",
                        _fake_git(MAIN_SHA, "main", True))
    metrics = tmp_path / "guard.prom"
    assert _mod.main(["--metrics-path", str(metrics), "--today", "2026-09-18"]) == 0
    out = capsys.readouterr().out
    assert "guard source:" in out and "== origin/main" in out
    text = metrics.read_text(encoding="utf-8")
    assert "stp_script_guard_due 0" in text and "guard source" not in text


def test_main_flags_off_main_source(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(_mod, "run_guard",
                        lambda exe, today: (0, _guard_payload("OK", 0)))
    monkeypatch.setattr(_mod.subprocess, "run",
                        _fake_git("b" * 40, "fix/elsewhere", True))
    rc = _mod.main(["--metrics-path", str(tmp_path / "g.prom")])
    err_out = capsys.readouterr()
    assert rc == 0, "判据来源不可信不改变判定码——它是归因，不是故障"
    assert "⚠" in err_out.out and "不是 origin/main" in err_out.out

