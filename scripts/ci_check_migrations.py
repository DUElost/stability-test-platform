#!/usr/bin/env python3
"""CI consistency check for Alembic migration chain and ORM model imports.

Run:  python scripts/ci_check_migrations.py
Exit: 0 on success, 1 on failure

Checks performed:
  1. Migration chain is linear (single head, no branches)
  2. All ORM model modules are imported in alembic/env.py
  3. No legacy model imports sneak into new-model modules
"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALEMBIC_ENV = ROOT / "backend" / "alembic" / "env.py"
VERSIONS_DIR = ROOT / "backend" / "alembic" / "versions"
NEW_MODEL_DIR = ROOT / "backend" / "models"

LEGACY_IMPORT = re.compile(r"from\s+backend\.models\.schemas\s+import")
NEW_MODEL_FILES = {
    "audit", "host", "job", "legacy", "notification", "schedule",
    "tool", "user", "workflow", "action_template", "enums",
}

from typing import List
errors: List[str] = []


def check_migration_chain():
    """Verify the migration chain is linear with a single head."""
    revisions = {}  # type: dict
    for f in VERSIONS_DIR.glob("*.py"):
        if f.name.startswith("__"):
            continue
        text = f.read_text(encoding="utf-8")
        rev_match = re.search(r'^revision[^=]*=\s*["\']([^"\']+)', text, re.M)
        down_match = re.search(r'^down_revision[^=]*=\s*(.+)', text, re.M)
        if not rev_match:
            errors.append(f"  {f.name}: missing revision ID")
            continue
        rev = rev_match.group(1)
        down_raw = down_match.group(1).strip() if down_match else "None"
        down = None
        if down_raw not in ("None", "none", '""', "''"):
            down = down_raw.strip("\"'")
        revisions[rev] = down

    heads = [r for r, d in revisions.items() if r not in revisions.values()]
    if len(heads) != 1:
        errors.append(f"  Migration chain has {len(heads)} heads: {heads} (expected 1)")
    else:
        print(f"  [OK] Linear chain, head = {heads[0]}")

    down_targets = set(revisions.values()) - {None}
    for dt in down_targets:
        if dt not in revisions:
            errors.append(f"  down_revision '{dt}' references a missing migration")


def check_env_imports():
    """Verify alembic/env.py imports all new model modules."""
    text = ALEMBIC_ENV.read_text(encoding="utf-8")
    for mod in NEW_MODEL_FILES:
        pattern = rf"import\s+backend\.models\.{mod}"
        if not re.search(pattern, text):
            errors.append(f"  alembic/env.py missing: import backend.models.{mod}")
    if not errors:
        print(f"  [OK] env.py imports all {len(NEW_MODEL_FILES)} model modules")


def check_no_legacy_in_new_modules():
    """Verify new model modules don't import from schemas.py."""
    for mod in NEW_MODEL_FILES:
        fpath = NEW_MODEL_DIR / f"{mod}.py"
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8")
        if LEGACY_IMPORT.search(text):
            errors.append(f"  {fpath.name}: imports from backend.models.schemas (should use canonical source)")
    if not errors:
        print("  [OK] No legacy imports in new model modules")


def main():
    print("=== Migration chain check ===")
    check_migration_chain()
    print("\n=== Alembic env.py import check ===")
    check_env_imports()
    print("\n=== Legacy import check ===")
    check_no_legacy_in_new_modules()

    if errors:
        print(f"\n{'='*60}")
        print(f"FAILED — {len(errors)} issue(s):")
        for e in errors:
            print(e)
        sys.exit(1)
    else:
        print(f"\n{'='*60}")
        print("ALL CHECKS PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
