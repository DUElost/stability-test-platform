from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an env file from a template if the target does not exist."
    )
    parser.add_argument("--template", required=True, help="Template file path")
    parser.add_argument("--target", required=True, help="Target env file path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    template = Path(args.template)
    target = Path(args.target)

    if not template.is_file():
        print(f"Template file not found: {template}", file=sys.stderr)
        return 1

    if target.exists():
        print(f"Env file already exists: {target}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, target)
    print(f"Created env file from template: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
