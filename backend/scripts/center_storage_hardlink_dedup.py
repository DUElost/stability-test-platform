#!/usr/bin/env python3
"""Retired center-storage hardlink dedup entry point (B8 G0-a, #3800).

Old logs are read-only after the split; the new CIFS log root cannot use hardlinks.
Keep a rejecting entry point for existing operational links, without DB/filesystem I/O.
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    print(
        "center-storage-interim-dedup retired by B8 G0-a (#3800): "
        "old logs are read-only; the new CIFS log root does not support hardlinks",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
