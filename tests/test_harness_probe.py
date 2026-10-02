"""Offline regressions for false-green Harness evidence; no model/API/DB calls."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.dev import harness_probe as probe
from tools.dev.source_anchor import SourceGuard


@pytest.mark.parametrize("text", [probe.PROBE_PROMPT, "Q1=是/否 Q2=是/否",
    "user: Q1=是 Q2=是", "Q1=是 Q2=是\nerror: failed", "Q1=是 Q2=是 Q3=两次",
    "Q1=是 Q1=否 Q2=是", "Q1=是", "", "收到"])
def test_echo_logs_incomplete_and_old_q3_are_unverified(text):
    assert not probe.grade(text)["graded"]


@pytest.mark.parametrize("text,q1,q2", [("Q1=是 Q2=否", True, False),
    ("Q1：否\nQ2：是", False, True), ("\n Q1 : 是\nQ2 = 是 \n", True, True)])
def test_complete_answer(text, q1, q2):
    assert probe.grade(text) == {"graded": True, "q1": q1, "q2": q2}


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(probe, "get_version", lambda form: "test CLI 1.2.3", raising=False)
    return next(f for f in probe.FORMS if f["id"] == "codebuddy")


@pytest.mark.parametrize("rc,stdout,stderr", [(1, "Q1=是 Q2=是", "tool failed"),
    (0, "", "Q1=是 Q2=是"), (0, "Q1=是 Q2=是", "tool failed"),
    (0, probe.PROBE_PROMPT, "")])
def test_cli_error_never_grades_an_answer(cli, monkeypatch, rc, stdout, stderr):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], rc, stdout, stderr))
    actual = probe.run_form(cli)
    assert not actual["graded"]
    assert probe.compare(actual, {"q1": True, "q2": True})


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired("cli", 1), FileNotFoundError(),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad byte")])
def test_cli_exceptions_unverified(cli, monkeypatch, exc):
    def fail(*a, **k):
        raise exc
    monkeypatch.setattr(probe.subprocess, "run", fail)
    actual = probe.run_form(cli, 1)
    assert probe.verdict(actual, {"q1": True, "q2": True}) == "UNVERIFIED"
    assert actual["error"] and not actual["graded"]


@pytest.mark.parametrize("rc,stderr", [(1, "not trusted"), (0, "Reading additional input from stdin...")])
def test_stderr_is_persisted_locally_and_kept_out_of_the_report(cli, monkeypatch, rc, stderr):
    """A stderr line must stay inspectable without becoming gradeable evidence."""
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], rc, "Q1=是 Q2=是", stderr))
    actual = probe.run_form(cli, 1, cwd="agent", mode="contract", stderr_dir="/tmp/probe-stderr-case")
    assert not actual["graded"] and probe.verdict(actual, {"q1": True, "q2": True}) == "UNVERIFIED"
    saved = Path(actual["stderr_file"])
    assert saved.name == "codebuddy_agent_contract.stderr" and saved.read_text() == stderr


def test_stderr_persistence_is_off_without_a_directory(cli, monkeypatch):
    monkeypatch.setattr(probe.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 1, "", "not trusted"))
    actual = probe.run_form(cli, 1)
    assert actual["stderr_file"] is None and actual["error"] == "exit=1"


def test_report_rows_carry_the_path_but_never_the_stderr_text(tmp_path, monkeypatch):
    monkeypatch.setattr(probe, "run_form", lambda *a, **k:
                        {**probe.grade("Q1=是 Q2=是"), "error": "stderr diagnostics",
                         "stderr_file": str(tmp_path / "x.stderr")})
    out = tmp_path / "report.json"
    probe.run_matrix("codex", 1, str(out), ["agent"], stderr_dir=str(tmp_path))
    row = json.loads(out.read_text())["results"][0]
    assert row["status"] == "UNVERIFIED" and row["stderr_file"] == str(tmp_path / "x.stderr")
    assert "stderr diagnostics" in out.read_text() and "Q1=是" not in out.read_text()


def test_keep_stderr_never_raises_when_the_directory_is_unusable(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    assert probe.keep_stderr(str(blocker), "codex", "root", "contract", "boom") is None


def test_cli_uses_argv_and_selected_cwd(cli, monkeypatch):
    seen = {}
    def run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, "Q1=是 Q2=是", "")
    monkeypatch.setattr(probe, "ROOT", "/repo with spaces")
    monkeypatch.setattr(probe.subprocess, "run", run)
    actual = probe.run_form(cli, cwd="aee", mode="autoload")
    assert probe.verdict(actual, {"q1": True, "q2": True}) == "PASS"
    assert seen["cwd"] == "/repo with spaces/backend/agent/aee"
    assert seen["argv"][-1] == probe.make_prompt("aee", "autoload")
    assert not seen.get("shell")


def events(*rows):
    return "\n".join(json.dumps(row) for row in rows)


def codex_answer(text="Q1=是 Q2=是"):
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


def claude_answer(text="Q1=是 Q2=是"):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text}


@pytest.mark.parametrize("protocol,output", [
    ("codex", events({"type": "item.completed", "item": {"type": "command_execution",
        "aggregated_output": "Q1=是 Q2=是", "exit_code": 0}}, {"type": "turn.completed"})),
    ("codex", events(codex_answer(), {"type": "turn.failed", "error": {"message": "failed"}})),
    ("codex", events({"type": "item.completed", "item": {"type": "command_execution", "exit_code": 1}},
        codex_answer(), {"type": "turn.completed"})),
    ("codex", events(codex_answer())),
    ("claude", events({"type": "user", "message": {"content": [{"type": "tool_result",
        "is_error": True, "content": "tool failed"}]}}, claude_answer())),
    ("claude", events({"type": "assistant", "message": {"content": "Q1=是 Q2=是"}})),
    ("claude", events(claude_answer(), claude_answer())),
    ("claude", events(claude_answer(), {"type": "user", "message": "later output"})),
    ("claude", "not JSON"), ("claude", "[]"),
])
def test_protocol_errors_and_nonfinal_roles_never_pass(protocol, output):
    assert probe.final_response(output, protocol) is None


def test_structured_final_answers():
    assert probe.final_response(events(codex_answer("working"), codex_answer(),
        {"type": "turn.completed"}), "codex") == "Q1=是 Q2=是"
    assert probe.final_response(events({"type": "user", "message": probe.PROBE_PROMPT},
        claude_answer()), "claude") == "Q1=是 Q2=是"


def test_three_state_verdict_error_beats_answer():
    actual = probe.grade("Q1=是 Q2=是")
    assert probe.verdict(actual, {"q1": True, "q2": True}) == "PASS"
    assert probe.verdict(actual, {"q1": False, "q2": True}) == "FAIL"
    actual["error"] = "exit=1"
    assert probe.verdict(actual, {"q1": True, "q2": True}) == "UNVERIFIED"


def test_each_cwd_is_asked_only_about_a_marker_its_own_contract_carries():
    """Guards the #3563 aee regression: a cell must not ask for a foreign layer's heading."""
    owner = {"root": None, "agent": "backend/agent/AGENTS.md",
             "aee": "backend/agent/aee/AGENTS.md"}
    for cwd, mark in probe.CWD_MARK.items():
        for mode in ("autoload", "contract"):
            prompt = probe.make_prompt(cwd, mode)
            assert mark in prompt
            # Exclusivity, not mere presence: asking for both markers is the regression.
            foreign = probe.AEE_MARK if mark == probe.SCOPED_MARK else probe.SCOPED_MARK
            assert foreign not in prompt, f"{cwd}/{mode} leaks another layer's marker"
        if owner[cwd] is not None:
            guard = SourceGuard.of_repo_path(owner[cwd]).anchored(mark)
            guard.assert_present(mark, why="该标记必须真在它自己那层契约里")
        else:
            # root is the negative control: it must not carry any scoped heading.
            root_guard = SourceGuard.of_repo_path("AGENTS.md").anchored(probe.ROOT_MARKS[0])
            root_guard.assert_absent(mark, why="#3563：root 契约不得出现 scoped 层标题")
    # The two markers live in different files — that is exactly why aee asks for one.
    aee_guard = SourceGuard.of_repo_path("backend/agent/aee/AGENTS.md").anchored(probe.AEE_MARK)
    aee_guard.assert_absent(probe.SCOPED_MARK,
                            why="#3563：AEE 层契约不得携带 Agent 层标题，否则 aee 格又变成跨层提问")


def test_modes_and_ide_baselines_are_separate():
    ids = {f["id"] for f in probe.FORMS}
    assert {"cursor", "codebuddy", "zcode"} <= ids
    for form in probe.FORMS:
        for cwd in probe.CWD_PATHS:
            assert probe.expected_for(form, cwd, "contract")["q1"] is True
            assert "Q3" not in probe.make_prompt(cwd, "contract")
    ide = next(f for f in probe.FORMS if f["id"] == "zcode")
    assert probe.expected_for(ide, "agent", "autoload")["q1"] is False
    assert "不要读取任何文件" in probe.make_prompt("agent", "autoload")
    assert "祖先继承" in probe.make_prompt("aee", "contract")


def test_retired_ide_forms_are_gone_from_the_matrix():
    """Owner 2026-10-01: Cursor IDE / CodeBuddy IDE leave the acceptance matrix."""
    ids = {f["id"] for f in probe.FORMS}
    assert not {"cursor-ide", "codebuddy-ide"} & ids
    assert [f["id"] for f in probe.FORMS if f.get("manual")] == ["zcode"]
    with pytest.raises(ValueError):
        probe.load_manual(_evidence_file([{"id": "cursor-ide", "cwd": "agent", "mode": "contract"}]))
    template = probe.manual_template(probe.FORMS, list(probe.CWD_PATHS), "contract")
    assert {r["id"] for r in template["records"]} == {"zcode"}


def _evidence_file(rows):
    import tempfile
    path = Path(tempfile.mkdtemp()) / "evidence.json"
    path.write_text(json.dumps({"root_version": "abc", "records": rows}))
    return str(path)


def test_manual_missing_evidence_has_no_actual(tmp_path):
    out = tmp_path / "report.json"
    assert probe.run_matrix("zcode", 1, str(out)) == 1
    rows = json.loads(out.read_text())["results"]
    assert len(rows) == 3
    assert {r["cwd"] for r in rows} == {"root", "agent", "aee"}
    assert all(r["status"] == "UNVERIFIED" and r["actual"] is None for r in rows)
    assert all(r["expected"]["q1"] for r in rows)


def manual_row():
    return {"id": "zcode", "cwd": "agent", "mode": "contract", "version": "3.11.2",
            "fresh_session": True, "response": "Q1=是 Q2=是", "tool_error": False,
            "evidence_source": "local new-session transcript 2026-10-01"}


@pytest.mark.parametrize("key,value", [("fresh_session", False), ("fresh_session", "true"),
    ("tool_error", True), ("tool_error", None), ("version", ""), ("evidence_source", ""),
    ("response", probe.PROBE_PROMPT)])
def test_manual_incomplete_or_error_unverified(key, value):
    row = manual_row()
    row[key] = value
    actual = probe.grade_manual(row, "abc", "abc")
    assert probe.verdict(actual, {"q1": True, "q2": True}) == "UNVERIFIED"


def test_manual_stale_revision_and_negative_response():
    row = manual_row()
    assert not probe.grade_manual(row, "old", "new")["graded"]
    assert not probe.grade_manual(row, "unknown", "unknown")["graded"]
    assert probe.verdict(probe.grade_manual(row, "abc", "abc"),
                         {"q1": True, "q2": True}) == "PASS"
    row["response"] = "Q1=否 Q2=是"
    assert probe.verdict(probe.grade_manual(row, "abc", "abc"),
                         {"q1": True, "q2": True}) == "FAIL"


@pytest.mark.parametrize("rows", [[manual_row(), manual_row()], [{"id": "codex", "cwd": "agent",
    "mode": "contract"}], [{"id": [], "cwd": "agent", "mode": "contract"}]])
def test_manual_duplicate_and_wrong_surface_rejected(tmp_path, rows):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps({"root_version": "abc", "records": rows}))
    with pytest.raises(ValueError):
        probe.load_manual(str(path))


def test_blank_template_does_not_invent_evidence():
    template = probe.manual_template(probe.FORMS, list(probe.CWD_PATHS), "contract")
    assert len(template["records"]) == 3
    assert all(r["response"] is None and r["version"] is None and not r["fresh_session"]
               for r in template["records"])


@pytest.mark.parametrize("answer,error,expected_rc", [("Q1=是 Q2=是", None, 0),
    ("Q1=否 Q2=是", None, 2), ("Q1=是 Q2=是", "exit=1", 1)])
def test_matrix_exit_status(tmp_path, monkeypatch, answer, error, expected_rc):
    monkeypatch.setattr(probe, "run_form", lambda *a, **k: {**probe.grade(answer), "error": error})
    out = tmp_path / "out.json"
    assert probe.run_matrix("codex", 1, str(out), ["agent"]) == expected_rc
    row = json.loads(out.read_text())["results"][0]
    assert row["status"] == {0: "PASS", 1: "UNVERIFIED", 2: "FAIL"}[expected_rc]


def test_invalid_manual_input_prevents_any_auto_call(tmp_path, monkeypatch):
    path = tmp_path / "bad.json"
    path.write_text("bad json")
    monkeypatch.setattr(probe, "run_form", lambda *a, **k: pytest.fail("must not call a CLI"))
    assert probe.run_matrix("codex", 1, None, manual_path=str(path)) == 1


def test_unknown_selection_and_source_change_unverified(monkeypatch):
    assert probe.run_matrix("unknown", 1, None) == 1
    assert probe.run_matrix("codex", 1, None, ["wrong"]) == 1
    heads = iter(["old", "new"])
    monkeypatch.setattr(probe, "_git_head", lambda: next(heads))
    monkeypatch.setattr(probe, "run_form", lambda *a, **k: probe.grade("Q1=是 Q2=是"))
    assert probe.run_matrix("codex", 1, None, ["agent"]) == 1


def test_self_test_and_root_quoting():
    assert probe.run_self_test() == 0
    # Shell syntax stays literal argv; root path need not be shell-safe.
    import shlex
    assert shlex.split(probe.build_command("sh {root}/script {prompt}", "$()`'", "/r ' space")) == [
        "sh", "/r ' space/script", "$()`'"]
