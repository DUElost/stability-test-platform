from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an env file from a template if the target does not exist."
    )
    parser.add_argument("--template", required=True, help="Template file path")
    parser.add_argument("--target", required=True, help="Target env file path")
    parser.add_argument(
        "--replace-placeholders",
        action="store_true",
        help="Replace placeholder secrets (JWT_SECRET_KEY/AGENT_SECRET/WS_TOKEN) on first creation.",
    )
    parser.add_argument(
        "--set",
        action="append",
        default=[],
        help="Override an env var as KEY=VALUE (can be repeated).",
    )
    return parser.parse_args()

_PLACEHOLDER_PATTERNS = (
    "change-me",
    "example",
    "your-",
    "password@localhost",
    "<",
)
_SECRET_KEYS = ("JWT_SECRET_KEY", "AGENT_SECRET", "WS_TOKEN")


def _looks_like_placeholder(val: str) -> bool:
    if not val:
        return True
    low = val.strip().strip('"').strip("'").lower()
    return any(p in low for p in _PLACEHOLDER_PATTERNS)


def _random_secret() -> str:
    # token_urlsafe produces URL-safe base64; good for env files and cookies/JWT keys.
    return secrets.token_urlsafe(48)


def _replace_placeholder_secrets(env_text: str) -> str:
    out_lines: list[str] = []
    had_trailing_newline = env_text.endswith("\n")

    for raw in env_text.splitlines():
        line = raw
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue

        # Best-effort support: allow "export KEY=VAL" in templates.
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()

        key, _, val = stripped.partition("=")
        key = key.strip()
        val = val.strip()

        if key in _SECRET_KEYS and _looks_like_placeholder(val):
            line = f"{key}={_random_secret()}"

        out_lines.append(line)

    out = "\n".join(out_lines)
    if had_trailing_newline:
        out += "\n"
    return out


def _apply_overrides(env_text: str, overrides: dict[str, str]) -> str:
    if not overrides:
        return env_text

    lines = env_text.splitlines()
    had_trailing_newline = env_text.endswith("\n")

    key_to_index: dict[str, int] = {}
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        key, _, _val = stripped.partition("=")
        key = key.strip()
        if key and key not in key_to_index:
            key_to_index[key] = i

    for k, v in overrides.items():
        new_line = f"{k}={v}"
        if k in key_to_index:
            lines[key_to_index[k]] = new_line
        else:
            lines.append(new_line)

    out = "\n".join(lines)
    if had_trailing_newline:
        out += "\n"
    return out


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
    content = template.read_text(encoding="utf-8")
    if args.replace_placeholders:
        content = _replace_placeholder_secrets(content)
    overrides: dict[str, str] = {}
    for item in args.set:
        if "=" not in item:
            print(f"Invalid --set value (expected KEY=VALUE): {item!r}", file=sys.stderr)
            return 2
        k, _, v = item.partition("=")
        overrides[k.strip()] = v.strip()
    content = _apply_overrides(content, overrides)
    target.write_text(content, encoding="utf-8")
    print(f"Created env file from template: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
