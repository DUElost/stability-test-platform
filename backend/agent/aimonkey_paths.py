"""Canonical AIMonkey resource paths (hot-update layout).

Bundle root after hot-update / repo checkout::

    <agent>/resources/aimonkey/AIMonkeyTest_20260317

Deploy example::

    /opt/stability-test-agent/agent/resources/aimonkey/AIMonkeyTest_20260317
"""

from __future__ import annotations

import os
from pathlib import Path

AGENT_DIR: Path = Path(__file__).resolve().parent
AIMONKEY_BUNDLE_NAME = "AIMonkeyTest_20260317"


def get_aimonkey_resource_root() -> Path:
    """Return the AIMonkey resource root directory (parent of the bundle)."""
    env_dir = os.environ.get("AIMONKEY_RESOURCE_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    return AGENT_DIR / "resources" / "aimonkey"


def resolve_aimonkey_bundle_dir(cfg: dict | None = None) -> Path:
    """Resolve the AIMonkeyTest bundle directory used by monkey scripts.

    Priority:
      1. ``cfg["aimonkey_dir"]`` when the path exists
      2. ``AIMONKEY_RESOURCE_DIR`` env (bundle subdir or ``MonkeyTest.py`` at root)
      3. ``AGENT_DIR/resources/aimonkey/AIMonkeyTest_20260317`` (hot-update default)
    """
    cfg = cfg or {}
    explicit = str(cfg.get("aimonkey_dir", "")).strip()
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            return path.resolve()

    resource_root = get_aimonkey_resource_root()
    if (resource_root / "MonkeyTest.py").is_file():
        return resource_root

    bundled = resource_root / AIMONKEY_BUNDLE_NAME
    if bundled.is_dir():
        return bundled
    return bundled
