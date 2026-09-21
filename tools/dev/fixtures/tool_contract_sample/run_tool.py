#!/usr/bin/env python3
"""STP Tool Contract 最小样板（ADR-0033 D2）——仅供 ``verify_tool_contract.py`` 靶子。

不是生产工具；实现四要素外壳：``--check-env`` / ``--context`` / ``--output-dir`` /
退出码 ``{0,1,2}`` / ``summary.json`` + ``artifacts/``。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _check_env() -> int:
    print(json.dumps({"ready": True, "tool": "tool_contract_sample"}, ensure_ascii=False))
    return 0


def _run(context: Path, output_dir: Path) -> int:
    if not context.is_file():
        print(json.dumps({"error": "context_missing", "path": str(context)}), file=sys.stderr)
        return 2
    try:
        ctx = json.loads(context.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": "context_invalid", "detail": str(exc)}), file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = output_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "note.txt").write_text("sample artifact\n", encoding="utf-8")

    summary = {
        "ok": True,
        "tool": "tool_contract_sample",
        "context_keys": sorted(ctx.keys()) if isinstance(ctx, dict) else [],
        "artifacts": ["artifacts/note.txt"],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-env", action="store_true")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    if args.check_env:
        return _check_env()
    if args.context is None or args.output_dir is None:
        print(
            json.dumps({"error": "require --context and --output-dir"}),
            file=sys.stderr,
        )
        return 2
    return _run(args.context, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
