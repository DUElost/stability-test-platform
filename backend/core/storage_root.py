"""中心存储本机挂载点解析（控制面）。

与 ``backend.agent.aee.paths.resolve_shared_storage_root`` 保持同语义。
放在 core 以免 ``artifact_paths`` / dedup / stats 反向依赖 ``backend.agent.aee``
（会连带加载 processor）。Agent 独立安装无 ``backend.core``，仍用 aee.paths 副本。
"""

from __future__ import annotations

import os


def resolve_shared_storage_root() -> str:
    """中心存储本机挂载点（NFS = CIFS = 同一台分享）。未配置返回空串。

    唯一主键 ``STP_AEE_NFS_ROOT``（#289：CIFS/WATCHER 弃用别名回落已删除，
    未设主键即视为未配置——调用方按各自契约报错或 503）。
    不回落到 ``STP_NFS_ROOT`` 或 HDD。
    """
    return (os.getenv("STP_AEE_NFS_ROOT") or "").strip()
