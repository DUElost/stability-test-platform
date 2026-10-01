#!/usr/bin/env python3
"""Manual Harness × cwd probe; PASS / FAIL / UNVERIFIED (#3516 G2).

Contract acceptance and no-read autoload diagnostics are separate experiments.
See docs/development/ai/harness-probes.md. No raw CLI logs are persisted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
CWD_PATHS = {"root": "", "agent": "backend/agent", "aee": "backend/agent/aee"}
SCOPED_MARK = "Agent 侧 scan / upload"
ROOT_MARKS = ("## 总原则", "## 提交前")
AEE_MARK = "AEE crash detection chain"
# Operator-local stderr evidence; gitignored and never referenced from the report body.
STDERR_DIR = str(Path(ROOT) / ".probe-evidence")


def make_prompt(cwd: str, mode: str) -> str:
    scope = f"『{SCOPED_MARK}』和『{AEE_MARK}』" if cwd == "aee" else f"『{SCOPED_MARK}』"
    instruction = (
        "不要读取任何文件，不要使用任何工具。仅凭当前已加载的指令上下文回答。"
        if mode == "autoload" else
        "先遵守已加载启动契约的祖先继承要求；仅可只读获取其要求的 AGENTS.md/CLAUDE.md，"
        "不要执行其他命令、读取其他文件或修改任何内容。然后根据获得的契约回答。"
    )
    return (instruction + "只输出两行，每行一个答案：Q1=<是或否>，Q2=<是或否>。"
            f"Q1：契约中是否存在 {ROOT_MARKS[0]} 和 {ROOT_MARKS[1]} 标题？"
            f"Q2：契约中是否存在标题{scope}？不要复述题目。")


# Historical offline entry; Q3 is deliberately removed.
PROBE_PROMPT = make_prompt("agent", "autoload")
PROBE_CWD = os.path.join(ROOT, CWD_PATHS["agent"])
_ANSWER = re.compile(r"\s*Q1\s*[=:：]\s*(是|否)\s+Q2\s*[=:：]\s*(是|否)\s*", re.I)


def grade(response: str) -> dict:
    """Only a complete final answer can be graded; never search logs or echoes."""
    match = _ANSWER.fullmatch(response) if isinstance(response, str) else None
    if not match:
        return {"graded": False, "q1": None, "q2": None}
    return {"graded": True, "q1": match[1] == "是", "q2": match[2] == "是"}


FORMS = [
    {"id": "claude-with-root", "desc": "Claude Code root wrapper (fallback)",
     "command": "sh {root}/tools/dev/claude_with_root.sh --output-format stream-json --verbose {prompt}",
     "version_command": ["claude", "--version"], "protocol": "claude"},
    {"id": "claude-subdir-plain", "desc": "Claude Code CLI",
     "command": "claude -p --output-format stream-json --verbose {prompt}",
     "version_command": ["claude", "--version"], "protocol": "claude"},
    {"id": "codex", "desc": "Codex CLI",
     "command": "codex exec --json --skip-git-repo-check {prompt}",
     "version_command": ["codex", "--version"], "protocol": "codex"},
    {"id": "cursor", "desc": "Cursor Agent CLI",
     "command": "cursor-agent -p {prompt}",
     "version_command": ["cursor-agent", "--version"], "protocol": "plain"},
    {"id": "opencode", "desc": "OpenCode CLI",
     "command": "opencode run {prompt}",
     "version_command": ["opencode", "--version"], "protocol": "plain"},
    {"id": "codebuddy", "desc": "CodeBuddy CLI",
     "command": "codebuddy -p {prompt}",
     "version_command": ["codebuddy", "--version"], "protocol": "plain"},
    {"id": "cursor-ide", "desc": "Cursor IDE", "manual": True, "command": None},
    {"id": "codebuddy-ide", "desc": "CodeBuddy IDE", "manual": True, "command": None},
    {"id": "zcode", "desc": "Zcode IDE", "manual": True, "command": None},
]


def expected_for(form: dict, cwd: str, mode: str) -> dict:
    # Historical workspace-only IDE observations are diagnostic baselines, NOT acceptance.
    root_visible = not (mode == "autoload" and form.get("manual") and cwd != "root"
                        and form["id"] in {"codebuddy-ide", "zcode"})
    return {"q1": root_visible, "q2": cwd != "root"}


def build_command(template: str, prompt: str, root: str) -> str:
    return template.format(prompt=shlex.quote(prompt), root=shlex.quote(root))


def _has_error(value) -> bool:
    if isinstance(value, list):
        return any(_has_error(v) for v in value)
    if not isinstance(value, dict):
        return False
    return (value.get("is_error") is True or bool(value.get("error"))
            or value.get("type") in {"error", "turn.failed"}
            or value.get("status") in {"failed", "error", "cancelled"}
            or (value.get("exit_code") is not None and value["exit_code"] != 0)
            or any(_has_error(v) for v in value.values()))


def final_response(stdout: str, protocol: str) -> str | None:
    """Recognize supported final-response protocols; tool errors invalidate answers."""
    if protocol == "plain":
        return stdout if grade(stdout)["graded"] else None
    try:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if not events or not all(isinstance(e, dict) for e in events) or _has_error(events):
            return None
    except (ValueError, TypeError, RecursionError):
        return None
    if protocol == "claude":
        finals = [e for e in events if e.get("type") == "result"]
        if (len(finals) != 1 or events[-1] is not finals[0]
                or finals[0].get("subtype") != "success" or finals[0].get("is_error") is not False):
            return None
        result = finals[0].get("result")
        return result if isinstance(result, str) else None
    if protocol == "codex":
        if events[-1].get("type") != "turn.completed":
            return None
        answers = [e["item"].get("text") for e in events if e.get("type") == "item.completed"
                   and isinstance(e.get("item"), dict) and e["item"].get("type") == "agent_message"]
        return answers[-1] if answers and isinstance(answers[-1], str) else None
    return None


def get_version(form: dict) -> str | None:
    try:
        proc = subprocess.run(form["version_command"], capture_output=True, text=True,
                              timeout=10, cwd=ROOT)
        version = proc.stdout.strip()
        # Do not copy arbitrary startup/config output into the report.
        if proc.returncode == 0 and re.fullmatch(r"[\w .()/,+-]{1,120}", version) and re.search(r"\d", version):
            return version
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return None


def keep_stderr(stderr_dir: str | None, form_id: str, cwd: str, mode: str, text: str) -> str | None:
    """Persist raw stderr locally so the operator can inspect it; never into the report."""
    if not stderr_dir:
        return None
    try:
        path = Path(stderr_dir) / f"{form_id}_{cwd}_{mode}.stderr"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)
    except OSError:
        return None


def run_form(form: dict, timeout_s: int = 180, cwd: str = "agent", mode: str = "contract",
             stderr_dir: str | None = None) -> dict:
    result = {"graded": False, "q1": None, "q2": None, "error": None, "version": None,
              "stderr_file": None}
    if not form.get("command"):
        return {**result, "error": "manual evidence missing"}
    version = get_version(form)
    result["version"] = version
    if version is None:
        return {**result, "error": "version unavailable"}
    argv = shlex.split(build_command(form["command"], make_prompt(cwd, mode), ROOT))
    start = time.monotonic()
    try:
        proc = subprocess.run(argv, cwd=os.path.join(ROOT, CWD_PATHS[cwd]), capture_output=True,
                              text=True, encoding="utf-8", errors="strict", timeout=timeout_s)
        # stderr is never an answer; unknown diagnostics require inspection.
        if proc.stderr.strip():
            result["stderr_file"] = keep_stderr(stderr_dir, form["id"], cwd, mode, proc.stderr)
        if proc.returncode != 0:
            result["error"] = f"exit={proc.returncode}"
        elif proc.stderr.strip():
            result["error"] = "stderr diagnostics; inspect locally"
        else:
            answer = final_response(proc.stdout, form["protocol"])
            result.update(grade(answer))
            if not result["graded"]:
                result["error"] = "no valid final answer or protocol/tool error"
    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except (OSError, UnicodeError) as exc:
        result["error"] = f"not-runnable: {type(exc).__name__}"
    result["seconds"] = round(time.monotonic() - start, 1)
    return result


def compare(actual: dict, expected: dict) -> list[str]:
    if actual.get("error") or not actual.get("graded"):
        return [f"不可判卷（{actual.get('error') or 'no answer'}）"]
    return [f"{k}={actual[k]}（预期 {v}）" for k, v in expected.items() if actual[k] != v]


def verdict(actual: dict, expected: dict) -> str:
    if actual.get("error") or not actual.get("graded"):
        return "UNVERIFIED"
    return "FAIL" if compare(actual, expected) else "PASS"


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=ROOT, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def manual_template(forms: list[dict], cwds: list[str], mode: str) -> dict:
    return {"root_version": _git_head(), "records": [
        {"id": f["id"], "cwd": cwd, "mode": mode, "version": None,
         "fresh_session": False, "response": None, "tool_error": None, "evidence_source": None}
        for f in forms if f.get("manual") for cwd in cwds]}


def load_manual(path: str) -> tuple[str, dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = {}
    manual_ids = {f["id"] for f in FORMS if f.get("manual")}
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise ValueError("invalid manual evidence structure")
    for row in data["records"]:
        if not isinstance(row, dict):
            raise ValueError("invalid manual row")
        key = (row.get("id"), row.get("cwd"), row.get("mode"))
        if (not all(isinstance(x, str) for x in key) or key[0] not in manual_ids
                or key[1] not in CWD_PATHS or key[2] not in {"contract", "autoload"}):
            raise ValueError("unknown manual matrix cell")
        if key in records:
            raise ValueError("duplicate manual matrix cell")
        records[key] = row
    return data.get("root_version"), records


def grade_manual(row: dict | None, source_version: str, head: str) -> dict:
    result = {"graded": False, "q1": None, "q2": None, "error": "manual evidence missing"}
    if row is None:
        return result
    valid = (head != "unknown" and source_version == head and row.get("fresh_session") is True
             and row.get("tool_error") is False
             and all(isinstance(row.get(k), str) and row[k].strip()
                     for k in ("version", "evidence_source")))
    if not valid:
        return {**result, "error": "incomplete, stale or errored manual evidence"}
    result.update(grade(row.get("response")))
    result["error"] = None if result["graded"] else "no valid manual final answer"
    result["version"] = row["version"]
    result["evidence_source"] = row["evidence_source"]
    return result


def run_matrix(only: str | None, timeout_s: int, json_out: str | None,
               cwds: list[str] | None = None, mode: str = "contract", manual_path: str | None = None,
               stderr_dir: str | None = None) -> int:
    forms = [f for f in FORMS if not only or f["id"] in only.split(",")]
    if only and set(only.split(",")) - {f["id"] for f in FORMS}:
        print("[UNVERIFIED] unknown form", file=sys.stderr)
        return 1
    cwds = list(CWD_PATHS) if cwds is None else cwds
    if not forms or not cwds or set(cwds) - set(CWD_PATHS):
        print("[UNVERIFIED] empty or invalid matrix selection", file=sys.stderr)
        return 1
    head = _git_head()
    source_version, manual = None, {}
    if manual_path:
        try:
            source_version, manual = load_manual(manual_path)
        except (OSError, ValueError, TypeError, RecursionError):
            print("[UNVERIFIED] invalid manual evidence file", file=sys.stderr)
            return 1
    results = []
    for form in forms:
        for cwd in cwds:
            expected = expected_for(form, cwd, mode)
            actual = (grade_manual(manual.get((form["id"], cwd, mode)), source_version, head)
                      if form.get("manual") else run_form(form, timeout_s, cwd, mode, stderr_dir))
            status = verdict(actual, expected)
            if head == "unknown" or _git_head() != head:
                status, actual["error"] = "UNVERIFIED", "source revision unavailable or changed"
            row = {"id": form["id"], "cwd": cwd, "mode": mode, "status": status,
                   "expected": expected,
                   "actual": {k: actual[k] for k in expected} if status != "UNVERIFIED" else None,
                   "error": actual.get("error"), "version": actual.get("version"),
                   "seconds": actual.get("seconds"), "evidence_source": actual.get("evidence_source"),
                   "stderr_file": actual.get("stderr_file")}
            results.append(row)
            print(f"[{status}] {form['id']} cwd={cwd} mode={mode} actual={row['actual']} "
                  f"error={row['error']} stderr_file={row['stderr_file']}", flush=True)
    counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "FAIL", "UNVERIFIED")}
    print(f"Matrix ({'inheritance acceptance' if mode == 'contract' else 'autoload diagnostic'}): {counts}")
    if json_out:
        Path(json_out).write_text(json.dumps({"date": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                             "root_version": head, "results": results},
                                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 2 if counts["FAIL"] else 1 if counts["UNVERIFIED"] else 0


def run_self_test() -> int:
    checks = [grade("Q1=是 Q2=否") == {"graded": True, "q1": True, "q2": False},
              grade("Q1：是\nQ2：否")["graded"], not grade("收到")["graded"],
              not grade(PROBE_PROMPT)["graded"], "Q3" not in PROBE_PROMPT,
              verdict({"graded": True, "q1": True, "q2": True, "error": "exit=1"},
                      {"q1": True, "q2": True}) == "UNVERIFIED",
              verdict(grade("Q1=否 Q2=是"), {"q1": True, "q2": True}) == "FAIL",
              verdict(grade("Q1=是 Q2=是"), {"q1": True, "q2": True}) == "PASS"]
    for form in FORMS:
        checks.append(bool(form["command"]) or form.get("manual") is True)
        if form["command"]:
            checks.append(PROBE_PROMPT in shlex.split(build_command(form["command"], PROBE_PROMPT, "/r space")))
    nasty = "a b 'c \"d $E `f`"
    checks.append(shlex.split(build_command("x -p {prompt} {root}", nasty, "/r space")) == ["x", "-p", nasty, "/r space"])
    if not all(checks):
        print("[SELFTEST-FAIL] offline answer/verdict/command checks", file=sys.stderr)
        return 1
    print("[OK] offline self-test; not real Harness acceptance")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", help="comma-separated form IDs; CLI and IDE are separate")
    ap.add_argument("--cwd", default="root,agent,aee", help="comma-separated root,agent,aee")
    ap.add_argument("--mode", choices=("contract", "autoload"), default="contract")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--manual-input", help="reviewed GUI evidence JSON")
    ap.add_argument("--manual-template", help="write blank GUI evidence JSON; do not run sessions")
    ap.add_argument("--stderr-dir", default=STDERR_DIR,
                    help="local gitignored directory for raw stderr; '' disables persistence")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return run_self_test()
    if args.timeout <= 0:
        ap.error("timeout must be positive")
    cwds = args.cwd.split(",")
    if set(cwds) - set(CWD_PATHS) or (args.only and set(args.only.split(",")) - {f["id"] for f in FORMS}):
        ap.error("unknown cwd/form")
    if args.manual_template:
        forms = [f for f in FORMS if not args.only or f["id"] in args.only.split(",")]
        Path(args.manual_template).write_text(json.dumps(manual_template(forms, cwds, args.mode),
                                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("[UNVERIFIED] blank manual template; no acceptance performed")
        return 1
    return run_matrix(args.only, args.timeout, args.json_out, cwds, args.mode, args.manual_input,
                      args.stderr_dir or None)


if __name__ == "__main__":
    sys.exit(main())
