"""DedupMergeEngine：控制面 B5 merge 的 vendor-CLI 薄接缝（ADR-0033 D4）。

接口只包 argv 构造与能力探测；round / 水位线 / flock / 发布 / 登记仍在
``dedup_scan.run_merge_sync``。Phase 2 选项 A：实现体是现态
``start_log_scan -merge_files_list``，不是 Scan-Result-GT。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional


class MergeArgv(NamedTuple):
    """``build_merge_argv`` 的结构化返回（与历史 ``Tuple[List[str], Optional[Path]]`` 等价）。"""

    argv: List[str]
    listfile: Optional[Path]


class DedupMergeEngine(ABC):
    """控制面多 host merge 的防腐适配器。

    不负责编排；不消费 Agent 侧 Scan-Result-GT（那是 B2）。
    """

    @abstractmethod
    def supports_merge_files_list(self, tool: Dict[str, str]) -> bool:
        """探测 vendor CLI 是否支持 ``-merge_files_list``。"""
        ...

    @abstractmethod
    def build_merge_argv(
        self,
        tool: Dict[str, str],
        org_files: List[str],
        side_argv: List[str],
    ) -> MergeArgv:
        """构造单次 merge 子进程 argv；调用方负责 unlink listfile。"""
        ...
