"""控制面 dedup merge 防腐层（ADR-0033 Phase 2 选项 A）。

样板挂在 **B5**（控制面多 host ``-merge_files_list``），不挂 Scan-Result-GT（B2）。
两平台共用同一 ``StartLogScanMergeEngine``（ADR-0032 D3）。
"""
from __future__ import annotations

from backend.services.dedup.base import DedupMergeEngine, MergeArgv
from backend.services.dedup.start_log_scan_merge import (
    StartLogScanMergeEngine,
    get_control_plane_merge_engine,
    reset_merge_capability_cache_for_tests,
)

__all__ = [
    "DedupMergeEngine",
    "MergeArgv",
    "StartLogScanMergeEngine",
    "get_control_plane_merge_engine",
    "reset_merge_capability_cache_for_tests",
]
