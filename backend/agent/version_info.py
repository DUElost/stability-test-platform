"""Read deployed Agent code revision from VERSION file."""

from __future__ import annotations

from pathlib import Path


def read_agent_code_revision() -> str:
    """Return git short SHA written by hot-update, or '' if unavailable."""
    candidates = [
        Path(__file__).resolve().parent / "VERSION",
        Path("/opt/stability-test-agent/agent/VERSION"),
    ]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            first_line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
            if first_line:
                return first_line.split()[0]
        except OSError:
            continue
    return ""
