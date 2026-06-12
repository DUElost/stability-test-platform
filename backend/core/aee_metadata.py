"""Backend-side re-export of the agent-local AEE metadata helpers.

唯一事实源是 backend/agent/aee/metadata.py(热更新只部署 backend/agent,
agent 运行时不得依赖 backend.core);backend 侧消费方经由本模块导入,
避免双副本漂移。
"""

from backend.agent.aee.metadata import (
    infer_aee_subtype_from_paths,
    normalize_aee_event_type,
    normalize_aee_subtype,
    normalize_package_name,
    parse_exp_main_summary,
)

__all__ = [
    "infer_aee_subtype_from_paths",
    "normalize_aee_event_type",
    "normalize_aee_subtype",
    "normalize_package_name",
    "parse_exp_main_summary",
]
