#!/usr/bin/env python3
"""Manual differential probe: real Bash argv vs destructive-Git checker (#3516).

No real Git command is executed: PATH and absolute-Git cases use a temporary
record-only executable. No profiles/env files are read. Not a default CI gate.
Run with project Python; --self-test runs a small real-shell subset. --checker
accepts a trusted historical checker file for mutation verification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
STYLES = ("plain", "single", "double", "escaped", "spliced", "ansi", "continuation")


def quote(word: str, style: str) -> str:
    if style == "plain":
        return shlex.quote(word)
    if style == "single":
        return "'" + word.replace("'", "'\"'\"'") + "'"
    if style == "double":
        return '"' + "".join("\\" + c if c in '\\$`"' else c for c in word) + '"'
    if style == "escaped":
        return "".join("\\" + c for c in word) or "''"
    if style == "spliced":
        middle = len(word) // 2
        return shlex.quote(word[:middle]) + '""' + shlex.quote(word[middle:])
    if style == "ansi":
        return "$'" + word.replace("\\", "\\\\").replace("'", "\\'") + "'"
    if style == "continuation":
        return shlex.quote(word) + "\\\n"
    raise ValueError(style)


def arguments():
    """Expected Git argv is generated independently of the checker parser."""
    dangerous = [
        ("hard", ["reset", "--hard"]), ("ha", ["reset", "--ha"]),
        ("har", ["reset", "--har"]), ("hard-tail", ["reset", "HEAD", "--hard"]),
        ("stash", ["stash"]), ("push", ["stash", "push", "-m", "wip"]),
        ("save", ["stash", "save"]), ("drop", ["stash", "drop"]),
        ("clear", ["stash", "clear"]), ("create", ["stash", "create"]),
        ("store", ["stash", "store", "deadbeef"]),
    ]
    for name, prefix in [
        ("config-env", ["--config-env", "core.abbrev=STP_PROBE_ABBREV"]),
        ("attr-source", ["--attr-source", "HEAD"]), ("config", ["-c", "x.y=1"]),
        ("directory", ["-C", "."]), ("git-dir", ["--git-dir", "."]),
        ("work-tree", ["--work-tree", "."]), ("namespace", ["--namespace", "x"]),
        ("no-pager", ["--no-pager"]), ("literal-paths", ["--literal-pathspecs"]),
        ("config-env-equals", ["--config-env=core.abbrev=STP_PROBE_ABBREV"]),
        ("combined", ["--no-pager", "-C", ".", "-c", "x.y=1"]),
    ]:
        dangerous.append((name, [*prefix, "reset", "--hard"]))
    for name, argv in dangerous:
        yield name, argv, True
    for name, argv in [
        ("status", ["status", "--short"]), ("soft", ["reset", "--soft", "HEAD"]),
        ("list", ["stash", "list"]), ("show", ["stash", "show"]),
        ("pop", ["stash", "pop"]), ("apply", ["stash", "apply"]),
        ("branch", ["stash", "branch", "recovery"]),
        ("config-env-read", ["--config-env", "core.abbrev=STP_PROBE_ABBREV", "status"]),
        ("commit-data", ["commit", "-m", "docs; git stash is forbidden"]),
        ("log-data", ["log", "--grep", "a|git stash|b"]),
    ]:
        yield name, argv, False


HOSTS = {
    "simple": lambda c: c,
    "absolute": lambda c: c,
    "list": lambda c: "true && " + c,
    "or-list": lambda c: "false || " + c,
    "semicolon": lambda c: ":; " + c,
    "newline": lambda c: ":\n" + c,
    "pipeline": lambda c: c + " | cat",
    "subshell": lambda c: "(" + c + ")",
    "group": lambda c: "{ " + c + "; }",
    "if": lambda c: "if true; then " + c + "; fi",
    "while": lambda c: "while true; do " + c + "; break; done",
    "for": lambda c: "for x in once; do " + c + "; done",
    "bash-c": lambda c: "bash -c " + shlex.quote(c),
    "bash-lc": lambda c: "bash --noprofile --norc -lc " + shlex.quote(c),
    "eval": lambda c: "eval " + shlex.quote(c),
    "env": lambda c: "env STP_PROBE_ABBREV=7 " + c,
    "command": lambda c: "command -- " + c,
    "exec": lambda c: "exec " + c,
    "timeout": lambda c: "timeout 5 " + c,
    "nice": lambda c: "nice -n 0 " + c,
    "nohup": lambda c: "nohup " + c + " </dev/null",
    "nested": lambda c: "env PROBE_NESTED=1 bash -c " + shlex.quote("command " + c),
    "substitution": lambda c: 'printf "%s" "$(' + c + ')"',
    "backticks": lambda c: 'printf "%s" `' + c.replace("\\", "\\\\").replace("`", "\\`") + '`',
    "double-backticks": lambda c: 'printf "%s" "`' + c.replace("\\", "\\\\").replace("`", "\\`") + '`"',
    "backtick-heredoc": lambda c: "cat <<EOF\n`" + c.replace("\\", "\\\\").replace("`", "\\`") + "`\nEOF\n",
    "process-substitution": lambda c: "cat <(" + c + ")",
    "heredoc-substitution": lambda c: "cat <<EOF\n$(" + c + ")\nEOF\n",
    "overflow-heredoc": lambda c: "cat <<EOF\n" + "$(" * 26 + c + ")" * 26 + "\nEOF\n",
    "shell-heredoc": lambda c: "bash <<'EOF'\n" + c + "\nEOF\n",
    "here-string": lambda c: "bash <<< " + shlex.quote(c),
    "data-heredoc": lambda c: "cat <<'EOF'\n" + c + "\nEOF\n",
}


@dataclass
class Case:
    name: str
    command: str
    argv: list[str] | None
    dangerous: bool
    known_gap: bool = False
    conservative_limit: bool = False


def cases(mock: Path, script: Path, small: bool = False):
    for name, argv, dangerous in arguments():
        for style in STYLES:
            for host, wrap in HOSTS.items():
                if small and (name not in {"hard", "config-env", "commit-data"}
                              or style not in {"plain", "ansi", "continuation"}
                              or host not in {"simple", "bash-c", "overflow-heredoc", "data-heredoc"}):
                    continue
                executable = str(mock) if host == "absolute" else "git"
                command = " ".join(quote(w, style) for w in [executable, *argv])
                data = host == "data-heredoc"
                yield Case(f"{name}/{style}/{host}", wrap(command), None if data else argv,
                           dangerous and not data,
                           conservative_limit=host == "overflow-heredoc" and name in {"commit-data", "log-data"})
    if not small:
        for name, command in {
            "pipe-to-shell": "printf '%s\\n' 'git reset --hard' | bash",
            "dynamic-bash-c": 'bash -c "$(printf %s \'git reset --hard\')"',
            "source-process": "source <(printf '%s\\n' 'git reset --hard')",
            "script-file": "bash " + shlex.quote(str(script)),
            "variable": "GIT=git; $GIT reset --hard",
            "braces": "git {reset,--hard}",
        }.items():
            yield Case(name, command, ["reset", "--hard"], True, known_gap=True)


def compare(case: Case, bash: str, env: dict, directory: Path, trace: Path, checker) -> dict:
    trace.unlink(missing_ok=True)
    try:
        result = subprocess.run([bash, "--noprofile", "--norc", "-c", case.command],
                                cwd=directory, env=env, capture_output=True, text=True, timeout=5)
        observed = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        expected = [] if case.argv is None else [case.argv]
        if result.returncode != 0 or observed != expected:
            return {"state": "UNVERIFIED", "name": case.name, "code": result.returncode, "argv": observed}
        blocked = bool(checker(case.command))
    except Exception as exc:
        return {"state": "UNVERIFIED", "name": case.name, "error": type(exc).__name__}
    if blocked == case.dangerous:
        state = "PASS"
    elif case.known_gap and case.dangerous:
        state = "KNOWN_GAP"
    elif case.conservative_limit and not case.dangerous:
        state = "CONSERVATIVE_BLOCK"
    else:
        state = "MISS" if case.dangerous else "FALSE_POSITIVE"
    return {"state": state, "name": case.name, "command": case.command}


def run(checker, small: bool = False) -> dict:
    bash = shutil.which("bash")
    if bash is None:
        return {"counts": {"UNVERIFIED": 1}, "details": [{"error": "bash unavailable"}]}
    with tempfile.TemporaryDirectory(prefix="stp-git-shell-probe-") as temporary:
        directory = Path(temporary)
        binary = directory / "bin"
        binary.mkdir()
        trace = directory / "argv.jsonl"
        mock = binary / "git"
        mock.write_text(f"#!{sys.executable}\nimport json, sys\n"
                        f"with open({str(trace)!r}, 'a') as output:\n"
                        " output.write(json.dumps(sys.argv[1:]) + '\\n')\n")
        mock.chmod(0o700)
        script = directory / "payload.sh"
        script.write_text("git reset --hard\n")
        # No ambient credentials, BASH_ENV, Git config or profile startup.
        env = {"PATH": f"{binary}:/usr/bin:/bin", "STP_PROBE_ABBREV": "7", "LC_ALL": "C"}
        counts, details = Counter(), []
        for case in cases(mock, script, small):
            result = compare(case, bash, env, directory, trace, checker)
            counts[result["state"]] += 1
            if result["state"] != "PASS":
                details.append(result)
        return {"counts": dict(counts), "details": details}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checker", type=Path, default=ROOT / "tools/dev/check_destructive_git.py")
    parser.add_argument("--json", type=Path, help="write full mismatch/gap evidence")
    parser.add_argument("--self-test", action="store_true", help="36 real Bash cases, no LLM/CI gate")
    args = parser.parse_args()
    checksum = None
    try:
        checksum = hashlib.sha256(args.checker.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location("git_checker_probe_target", args.checker)
        if spec is None or spec.loader is None:
            raise ImportError("checker has no module loader")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        checker = module.find_blocked
        if not callable(checker):
            raise TypeError("find_blocked is not callable")
    except (Exception, SystemExit) as exc:
        report = {"counts": {"UNVERIFIED": 1}, "details": [
            {"state": "UNVERIFIED", "stage": "checker-load", "error": type(exc).__name__},
        ]}
    else:
        report = run(checker, args.self_test)
        try:
            if hashlib.sha256(args.checker.read_bytes()).hexdigest() != checksum:
                raise ValueError("checker changed during probe")
        except Exception as exc:
            report["counts"]["UNVERIFIED"] = report["counts"].get("UNVERIFIED", 0) + 1
            report["details"].append({"state": "UNVERIFIED", "stage": "checker-source",
                                      "error": type(exc).__name__})
    report["checker"] = str(args.checker.absolute())
    report["sha256"] = checksum
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "details"}, ensure_ascii=False))
    for detail in report["details"][:10]:
        print(json.dumps(detail, ensure_ascii=False))
    return int(any(report["counts"].get(state) for state in ("MISS", "FALSE_POSITIVE", "UNVERIFIED")))


if __name__ == "__main__":
    sys.exit(main())
