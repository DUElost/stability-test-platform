#!/usr/bin/env python3
from __future__ import annotations

import json


def main() -> None:
    # The pipeline engine treats exit_code==0 as success; payload is optional.
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main()

