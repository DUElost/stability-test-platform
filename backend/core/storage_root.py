"""中心存储本机挂载点解析（控制面）。

与 ``backend.agent.aee.paths.resolve_shared_storage_root`` 保持同语义。
放在 core 以免 ``artifact_paths`` / dedup / stats 反向依赖 ``backend.agent.aee``
（会连带加载 processor）。Agent 独立安装无 ``backend.core``，仍用 aee.paths 副本。
"""

from __future__ import annotations

import os


def resolve_shared_storage_root() -> str:
    """日志根本机挂载点。未配置返回空串。

    唯一主键 ``STP_AEE_NFS_ROOT``（#289：CIFS/WATCHER 弃用别名回落已删除，
    未设主键即视为未配置——调用方按各自契约报错或 503）。
    不回落到 ``STP_NFS_ROOT`` 或 HDD。
    """
    return (os.getenv("STP_AEE_NFS_ROOT") or "").strip()


def resolve_control_plane_storage_root() -> str:
    """分发内容与 Job 日志根；未设置时保持拆根前的路径（B8 G0-a）。"""
    return (os.getenv("STP_CONTROL_PLANE_STORAGE_ROOT") or "").strip() or resolve_shared_storage_root()


def resolve_legacy_log_storage_root() -> str:
    """只读旧日志根，仅供下载校验，不参与写入或 retention。"""
    return (os.getenv("STP_LEGACY_LOG_STORAGE_ROOT") or "").strip()
