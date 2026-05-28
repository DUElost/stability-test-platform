from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


def build_subprocess_env(
    env_file: Path,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    if env_file.exists():
        for key, value in dotenv_values(env_file).items():
            if value is not None:
                env[key] = value
    return env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a command with backend .env values overriding parent env."
    )
    parser.add_argument(
        "--env-file",
        default="backend/.env",
        help="Path to env file whose values should override parent env.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command to execute after '--'.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("Missing command to execute.", file=sys.stderr)
        return 2

    env = build_subprocess_env(Path(args.env_file))
    completed = subprocess.run(command, env=env, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
