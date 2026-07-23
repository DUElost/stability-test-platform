"""Allowlisted .env key sync for agent hot-update.

Hot-update rsyncs agent code; ``$INSTALL_DIR/.env`` is merged line-by-line for
**fleet-wide** keys only.  Per-host identity, connectivity, and machine-local
paths are never overwritten.

Control plane operators set fleet defaults once (backend ``.env``); each
``POST .../hot-update`` propagates those values plus install-dir-derived paths.
"""

from __future__ import annotations

import os

REMOTE_INSTALL_DIR_DEFAULT = "/opt/stability-test-agent"

# Per-host / machine-local — never touched by hot-update env merge.
# AGENT_SECRET is updated only when sync_agent_secret=true (separate path).
PROTECTED_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HOST_ID",
        "API_URL",
        "WS_URL",
        "AUTO_REGISTER_HOST",
        "AUTO_REGISTER_MAX_RETRIES",
        "AUTO_REGISTER_RETRY_DELAY",
        "ANDROID_ADB_SERVER_PORT",
        "ADB_PATH",
        "MOUNT_POINTS",
        "AGENT_SECRET",
        "STP_STATIC_DEVICE_SERIALS",
    }
)

# Fleet-wide keys: synced when the control plane has a non-empty value.
_FLEET_ENV_KEYS: tuple[str, ...] = (
    "STP_AEE_NFS_ROOT",
    "STP_AEE_CIFS_ROOT",
    "STP_AEE_LOCAL_ROOT",
    "STP_NFS_ROOT",
    "STP_DEDUP_SCAN_PYTHON",
    "STP_DEDUP_SCAN_SCRIPT",
    "STP_DEDUP_SCAN_TAG",
    "STP_DEDUP_AUTO_SCAN",
    "PIP_INDEX_URL",
    "LOG_LEVEL",
    "STP_WATCHER_ENABLED",
    "STP_AGENT_PIP_INDEX_URL",
)


def _install_dir_env_overrides(install_dir: str) -> dict[str, str]:
    """Paths derived from the standard agent install layout."""
    root = install_dir.rstrip("/")
    return {
        "AGENT_INSTALL_DIR": root,
        "AIMONKEY_RESOURCE_DIR": f"{root}/agent/resources/aimonkey",
        "LOG_DIR": f"{root}/logs",
        "PYTHONPATH": root,
    }


def _fleet_env_overrides_from_control_plane() -> dict[str, str]:
    """Read fleet defaults from the control-plane process environment."""
    overrides: dict[str, str] = {}
    for key in _FLEET_ENV_KEYS:
        val = os.getenv(key, "").strip()
        if val:
            overrides[key] = val

    # Legacy alias used by some agent scripts; mirror NFS root when configured.
    nfs_root = (
        os.getenv("STP_NFS_ROOT", "").strip()
        or os.getenv("STP_AEE_NFS_ROOT", "").strip()
    )
    if nfs_root:
        overrides.setdefault("STP_NFS_ROOT", nfs_root)
        overrides.setdefault("STP_AEE_NFS_ROOT", nfs_root)

    pip_index = os.getenv("STP_AGENT_PIP_INDEX_URL", "").strip()
    if pip_index:
        overrides.setdefault("PIP_INDEX_URL", pip_index)

    return overrides


def hot_update_env_overrides(
    install_dir: str = REMOTE_INSTALL_DIR_DEFAULT,
) -> dict[str, str]:
    """Return allowlisted .env keys and canonical values for hot-update."""
    overrides: dict[str, str] = {}
    overrides.update(_install_dir_env_overrides(install_dir))
    overrides.update(_fleet_env_overrides_from_control_plane())

    for key in PROTECTED_ENV_KEYS:
        overrides.pop(key, None)

    return overrides


def merge_env_overrides(
    lines: list[str],
    overrides: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Merge allowlisted overrides into .env lines.

    Preserves comments, blank lines, and keys outside the allowlist.
    Returns ``(new_lines, updated_keys)``.
    """
    if not overrides:
        return list(lines), []

    seen: set[str] = set()
    updated_keys: list[str] = []
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key, _, _ = line.partition("=")
        key = key.strip()
        if key in PROTECTED_ENV_KEYS:
            new_lines.append(line)
            continue
        if key in overrides:
            new_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
            updated_keys.append(key)
        else:
            new_lines.append(line)

    for key, val in overrides.items():
        if key in PROTECTED_ENV_KEYS or key in seen:
            continue
        new_lines.append(f"{key}={val}")
        updated_keys.append(key)

    return new_lines, updated_keys
