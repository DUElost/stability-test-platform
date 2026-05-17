from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


AGENT_SECRET_KEY = "AGENT_SECRET"
AGENT_SECRET_PLACEHOLDER = "change-me-in-production"
MIN_SECRET_LENGTH = 16


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure backend/.env contains a usable local AGENT_SECRET."
    )
    parser.add_argument("--env-file", required=True, help="Path to backend env file")
    return parser.parse_args()


def _needs_secret(value: str) -> bool:
    candidate = value.strip()
    return not candidate or candidate == AGENT_SECRET_PLACEHOLDER or len(candidate) < MIN_SECRET_LENGTH


def _generate_secret() -> str:
    return secrets.token_urlsafe(24)


def _extract_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def ensure_agent_secret(env_file: Path) -> int:
    if not env_file.is_file():
        print(f"Env file not found: {env_file}", file=sys.stderr)
        return 1

    original = env_file.read_text(encoding="utf-8")
    newline = _extract_newline(original)
    lines = original.splitlines()

    target_idx = None
    current_value = ""
    for idx, line in enumerate(lines):
        if line.startswith(f"{AGENT_SECRET_KEY}="):
            target_idx = idx
            current_value = line.split("=", 1)[1]
            break

    if target_idx is not None and not _needs_secret(current_value):
        print(f"{AGENT_SECRET_KEY} already configured in {env_file}")
        return 0

    new_secret = _generate_secret()
    secret_line = f"{AGENT_SECRET_KEY}={new_secret}"

    if target_idx is None:
        if lines and lines[-1] != "":
            lines.append(secret_line)
        else:
            if not lines:
                lines = [secret_line]
            else:
                lines.insert(len(lines) - 1, secret_line)
    else:
        lines[target_idx] = secret_line

    updated = newline.join(lines)
    if original.endswith(("\n", "\r\n")):
        updated += newline

    env_file.write_text(updated, encoding="utf-8")
    print(f"Generated local {AGENT_SECRET_KEY} in {env_file}")
    return 0


def main() -> int:
    args = parse_args()
    return ensure_agent_secret(Path(args.env_file))


if __name__ == "__main__":
    raise SystemExit(main())
