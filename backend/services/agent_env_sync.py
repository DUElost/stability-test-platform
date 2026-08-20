"""Allowlisted .env key sync for agent hot-update.

Hot-update rsyncs agent code; ``$INSTALL_DIR/.env`` is merged line-by-line for
**fleet-wide** keys only.  Per-host identity, connectivity, and machine-local
paths are never overwritten.

Control plane operators set fleet defaults once (backend ``.env``); each
``POST .../hot-update`` propagates those values plus install-dir-derived paths.

Keys the control plane also consumes itself must not be synced verbatim when
the two roles need different values — those go through ``STP_AGENT_``-prefixed
source names instead (see ``_AGENT_SCOPED_ENV_KEYS``).
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
        # Machine-local L1 path (NVMe+HDD hosts → /mnt/hdd/aee_events;
        # SSD-only / single-disk hosts differ). Never fleet-overwrite.
        "STP_AEE_LOCAL_ROOT",
    }
)

# Fleet-wide keys: synced when the control plane has a non-empty value.
# Only keys whose correct value is identical on the control plane and on every
# agent belong here.
_FLEET_ENV_KEYS: tuple[str, ...] = (
    "STP_AEE_NFS_ROOT",
    "STP_DEDUP_SCAN_TAG",
    "STP_DEDUP_AUTO_SCAN",
    "LOG_LEVEL",
    "STP_WATCHER_ENABLED",
    # ADR-0028: same value on control plane and every agent (#218).
    # Unset on the control plane → not pushed (agents keep local value).
    "STP_DEVICE_LOG_EVENT_ENABLED",
    "STP_EVENT_UPLOADER_ENABLED",
    # ADR-0028 方案 A：0=仅上传 UPLOAD_PENDING（过滤模型）；1=上传全部 LOCAL（全量模型）
    "STP_EVENT_UPLOADER_CONTINUOUS",
    # MTBF P0（ADR-0030 D6）：套件 testpoint 期望数，全 fleet 同值。
    # 控制面设置一次，hot-update 下发；脚本侧默认 0=只报绝对数。
    # 注意：STP_MTBF_TASK_TIMES 故意**不**在此列——冒烟期=1、生产=100，
    # 且未来相机套件按项目分化，属 host 级手工 .env（见 mtbf-api.md）。
    "STP_MTBF_EXPECTED_TESTPOINT_COUNT",
)

# Agent-scoped keys: the control plane holds the *agent-side* value under a
# ``STP_AGENT_``-prefixed name, which hot-update writes to the unprefixed key.
# Required wherever both roles read the same key name but need different values
# — the scan tool lives at a different path on the control plane than on the
# agents, so syncing the control plane's own value breaks every agent.
_AGENT_SCOPED_ENV_KEYS: dict[str, str] = {
    "STP_AGENT_PIP_INDEX_URL": "PIP_INDEX_URL",
    "STP_AGENT_DEDUP_SCAN_PYTHON": "STP_DEDUP_SCAN_PYTHON",
    "STP_AGENT_DEDUP_SCAN_SCRIPT": "STP_DEDUP_SCAN_SCRIPT",
}

# Synced keys whose value must be an existing path on the agent. Verified
# after the .env merge so a misconfigured fleet default surfaces at push time
# instead of as a silently empty archive several runs later.
AGENT_PATH_ENV_KEYS: frozenset[str] = frozenset(
    {
        "STP_AEE_LOCAL_ROOT",
        "STP_AEE_NFS_ROOT",
        "STP_DEDUP_SCAN_PYTHON",
        "STP_DEDUP_SCAN_SCRIPT",
        "STP_NFS_ROOT",
    }
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

    for source_key, agent_key in _AGENT_SCOPED_ENV_KEYS.items():
        val = os.getenv(source_key, "").strip()
        if val:
            overrides[agent_key] = val

    # Legacy script env: subprocesses still read STP_NFS_ROOT. Mirror the
    # 中心存储 mount — never the control plane's own STP_NFS_ROOT.
    aee_nfs_root = os.getenv("STP_AEE_NFS_ROOT", "").strip()
    if aee_nfs_root:
        overrides.setdefault("STP_NFS_ROOT", aee_nfs_root)

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


def agent_path_keys_to_verify(overrides: dict[str, str]) -> list[str]:
    """Keys in ``overrides`` whose values must exist on the agent filesystem."""
    return sorted(
        key for key, val in overrides.items() if key in AGENT_PATH_ENV_KEYS and val
    )


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
