"""Migrate Agent AEE state keys from scan_aee:* to watcher:aee:*.

Usage:
    python -m backend.scripts.migrate_watcher_aee_state_keys --db-path /path/to/agent_state.db --dry-run
    STP_AGENT_STATE_DB=/path/to/agent_state.db python -m backend.scripts.migrate_watcher_aee_state_keys
"""

from __future__ import annotations

import argparse
import json
import os

from backend.agent.aee.state_migration import migrate_legacy_aee_state_keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=os.getenv("STP_AGENT_STATE_DB"),
        help="Path to agent_state.db; defaults to STP_AGENT_STATE_DB",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.db_path:
        raise SystemExit("--db-path is required when STP_AGENT_STATE_DB is not set")
    summary = migrate_legacy_aee_state_keys(str(args.db_path), dry_run=bool(args.dry_run))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
