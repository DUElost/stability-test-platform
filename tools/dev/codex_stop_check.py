#!/usr/bin/env python3
"""Codex Stop quality feedback; no edit veto, imports of application code, or installs.

All classified results emit Stop JSON and exit 0 for feedback delivery.
Quality status is the explicit PASS / FAIL / UNVERIFIED message, not the hook's
completed status or transport exit code. Never request a continuation or stop.
Project .venv launches this file; Python syntax here is not CI-version acceptance.
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

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 45


def run_check(kind: str) -> tuple[str, str]:
    if kind == "typecheck":
        frontend = ROOT / "frontend"
        compiler = frontend / "node_modules/typescript/bin/tsc"
        node = shutil.which("node")
        if not node or not compiler.is_file() or not (frontend / "tsconfig.json").is_file():
            return "UNVERIFIED", "Node / installed TypeScript / tsconfig missing; initialize the worktree dependencies"
        command = [node, str(compiler), "--noEmit"]
        cwd = frontend
    else:
        backend = ROOT / "backend"
        if not backend.is_dir() or not any(backend.rglob("*.py")):
            return "UNVERIFIED", "backend Python source missing"
        cwd = ROOT
        command = [sys.executable, "-I", "-m", "compileall", str(backend), "-q"]
    try:
        # No pyc in the source tree or published script-version directories.
        with tempfile.TemporaryDirectory(prefix="stp-codex-pyc-") as cache:
            if kind == "compileall":
                command[1:1] = ["-X", f"pycache_prefix={cache}"]
            proc = subprocess.run(command, cwd=cwd, capture_output=True, text=True,
                                  timeout=TIMEOUT, env=os.environ.copy())
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        return "UNVERIFIED", type(exc).__name__
    if proc.returncode:
        detail = (proc.stdout + proc.stderr).strip()[-4000:]
        return "FAIL", f"exit={proc.returncode}\n{detail}"
    return "PASS", "check completed; required CI checks remain separate"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("typecheck", "compileall"))
    args = parser.parse_args()
    try:
        state, detail = run_check(args.kind)
    except OSError as exc:
        state, detail = "UNVERIFIED", type(exc).__name__
    message = f"[{state}] Codex Stop {args.kind}: {detail}"
    if state != "PASS":
        message += (
            f"\nQuality status: {state} (not a quality PASS). "
            "Hook completion only confirms feedback delivery."
        )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
