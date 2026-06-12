"""Agent-local legacy AEE constants.

The full backend imports backend.core.legacy_aee; hot-updated agent hosts only
receive the agent package, so this module falls back to a local value there.
"""

from __future__ import annotations

try:
    from backend.core.legacy_aee import LEGACY_AEE_SCRIPT_NAMES
except ImportError:
    LEGACY_AEE_SCRIPT_NAMES = frozenset({"scan_aee", "export_mobilelogs"})
