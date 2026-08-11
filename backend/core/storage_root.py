"""中心存储本机挂载点解析（控制面）。

与 ``backend.agent.aee.paths.resolve_shared_storage_root`` 保持同语义。
放在 core 以免 ``artifact_paths`` / dedup / stats 反向依赖 ``backend.agent.aee``
（会连带加载 processor）。Agent 独立安装无 ``backend.core``，仍用 aee.paths 副本。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_shared_root_alias_warned: set[str] = set()


def resolve_shared_storage_root() -> str:
    """中心存储本机挂载点（NFS = CIFS = 同一台分享）。未配置返回空串。

    主键 ``STP_AEE_NFS_ROOT``。``STP_WATCHER_NFS_BASE_DIR`` /
    ``STP_AEE_CIFS_ROOT`` 仅作弃用别名（未设主键时回落，并打一次 WARNING）。
    不回落到 ``STP_NFS_ROOT`` 或 HDD。
    """
    primary = (os.getenv("STP_AEE_NFS_ROOT") or "").strip()
    if primary:
        return primary
    for alias in ("STP_WATCHER_NFS_BASE_DIR", "STP_AEE_CIFS_ROOT"):
        raw = (os.getenv(alias) or "").strip()
        if not raw:
            continue
        if alias not in _shared_root_alias_warned:
            _shared_root_alias_warned.add(alias)
            logger.warning(
                "shared_storage_root_alias_deprecated alias=%s use=STP_AEE_NFS_ROOT",
                alias,
            )
        return raw
    return ""


def resolve_legacy_shared_storage_root() -> str:
    """存储切换窗口内的旧中心存储根（ADR-0028 D8）。未配置返回空串。"""
    return (os.getenv("STP_AEE_NFS_ROOT_LEGACY") or "").strip()
