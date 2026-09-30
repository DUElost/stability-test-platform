#!/usr/bin/env python3
"""Opt-in Git hook status and isolated behavior self-check (#3516).

Default: read current Git hook path and executable files, no config/index/ref
writes. --self-test requires configured repository hooks and checks copies in a
temporary repository. It never enables hooks or runs commits in the caller repo.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HOOKS = ("pre-commit", "reference-transaction")


def git(repo: Path, *args: str, env=None):
    return subprocess.run(["git", "-C", str(repo), *args], env=env,
                          text=True, capture_output=True, timeout=15)


def output(repo: Path, *args: str, env=None) -> str:
    result = git(repo, *args, env=env)
    if result.returncode:
        raise RuntimeError("Git query failed")
    return result.stdout.strip()


def status(repo: Path) -> dict:
    root = Path(output(repo, "rev-parse", "--show-toplevel")).resolve()
    effective = Path(output(root, "rev-parse", "--path-format=absolute", "--git-path", "hooks")).resolve()
    expected = (root / ".githooks").resolve()
    report = {"root": str(root), "hooks_path": str(effective), "state": "DISABLED"}
    if effective != expected:
        return report
    missing = [name for name in HOOKS if not (effective / name).is_file()
               or not os.access(effective / name, os.X_OK)]
    report.update(state="UNVERIFIED" if missing else "CONFIGURED", unavailable=missing)
    return report


def self_check(root: Path) -> dict:
    """Only write to a disposable Git repo; no stash/reset or caller Git writes."""
    if os.name != "posix":
        raise RuntimeError("behavior self-check requires POSIX")
    with tempfile.TemporaryDirectory(prefix="stp-git-hooks-") as temporary:
        repo = Path(temporary)
        hooks = repo / ".githooks"
        hooks.mkdir()
        for name in HOOKS:
            shutil.copy2(root / ".githooks" / name, hooks / name)
        tools = repo / "tools" / "dev"
        tools.mkdir(parents=True)
        shutil.copy2(root / "tools/dev/check-internal-ip-leak.py", tools)
        shutil.copy2(root / ".gitattributes", repo)
        template = repo / "empty-template"
        template.mkdir()
        # No inherited Git config, GIT_DIR/INDEX_FILE, signing, credentials or hooks.
        env = {"PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin", "HOME": temporary,
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C"}
        output(repo, "init", "-q", "--template=" + str(template), env=env)
        output(repo, "config", "--local", "core.hooksPath", ".githooks", env=env)
        (repo / "README.md").write_text("isolated hook probe\n")
        output(repo, "add", "README.md", env=env)
        commit = ("-c", "user.name=Hook Probe", "-c", "user.email=probe@example.invalid",
                  "-c", "commit.gpgsign=false", "commit", "-qm")
        output(repo, *commit, "baseline", env=env)
        head = output(repo, "rev-parse", "HEAD", env=env)
        (repo / "probe.py").write_text("# pollution probe\n" + "\n" * 40 + "x = 1\n")
        output(repo, "add", "probe.py", env=env)
        rejected = git(repo, *commit, "pollution", env=env)
        pollution = (rejected.returncode != 0 and "[BLOCK] probe.py" in rejected.stdout + rejected.stderr
                     and output(repo, "rev-parse", "HEAD", env=env) == head)
        transaction = git(repo, "update-ref", "refs/stash", head, env=env)
        observed = (transaction.returncode == 0
                    and "refs/stash prepared:" in transaction.stderr
                    and "refs/stash committed:" in transaction.stderr
                    and output(repo, "rev-parse", "refs/stash", env=env) == head)
        return {"state": "PASS" if pollution and observed else "FAIL",
                "checks": {"pollution_commit_blocked": pollution,
                           "stash_transaction_warned_without_blocking": observed}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    try:
        report = status(args.repo)
        if args.self_test:
            if report["state"] == "CONFIGURED":
                report.update(self_check(Path(report["root"])))
            else:
                report.update(state="UNVERIFIED", reason="repository hooks are not configured/executable")
    except Exception as exc:
        report = {"state": "UNVERIFIED", "error": type(exc).__name__}
    print(json.dumps(report, ensure_ascii=False))
    return int(report["state"] in {"FAIL", "UNVERIFIED"})


if __name__ == "__main__":
    sys.exit(main())
