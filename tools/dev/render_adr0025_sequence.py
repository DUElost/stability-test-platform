#!/usr/bin/env python3
"""Render ADR-0025 §2 human-readable sequence diagram (PNG + SVG).

Extracts the first ```mermaid block from
``docs/design/2026-adr-0025-log-flow-sequence.md`` and renders it with
@mermaid-js/mermaid-cli (``mmdc`` via npx) into
``docs/design/assets/adr-0025-log-flow-sequence-human.{png,svg}``.

Usage::

    python tools/dev/render_adr0025_sequence.py

Requires network on first run (npx downloads the CLI + headless Chromium).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = REPO_ROOT / "docs" / "design" / "2026-adr-0025-log-flow-sequence.md"
ASSET_STEM = (
    REPO_ROOT / "docs" / "design" / "assets" / "adr-0025-log-flow-sequence-human"
)


def extract_first_mermaid(source: str) -> str:
    """Return the first ```mermaid fenced block body."""
    marker = "```mermaid\n"
    start = source.find(marker)
    if start < 0:
        raise SystemExit("mermaid fence not found in %s" % DOC_PATH)
    body_start = start + len(marker)
    end = source.find("\n```", body_start)
    if end < 0:
        raise SystemExit("unterminated mermaid fence in %s" % DOC_PATH)
    return source[body_start:end]


def main() -> int:
    if not DOC_PATH.is_file():
        raise SystemExit("missing doc: %s" % DOC_PATH)
    diagram = extract_first_mermaid(DOC_PATH.read_text(encoding="utf-8"))

    ASSET_STEM.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="stp-adr0025-") as tmp:
        mmd = Path(tmp) / "diagram.mmd"
        mmd.write_text(diagram, encoding="utf-8")
        for suffix in ("png", "svg"):
            out = ASSET_STEM.with_suffix("." + suffix)
            subprocess.run(
                [
                    "npx", "-y", "@mermaid-js/mermaid-cli@11",
                    "-i", str(mmd),
                    "-o", str(out),
                    "-b", "white",
                ],
                cwd=REPO_ROOT,
                check=True,
            )
            print("rendered %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
