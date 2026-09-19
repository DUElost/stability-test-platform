"""B5 样板：包现态 ``start_log_scan -merge_files_list``（ADR-0033 Phase 2 选项 A）。

mtk / unisoc 分区共用本引擎（ADR-0032 D3）。Scan-Result-GT 仍只在 Agent B2。
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from backend.services.dedup.base import DedupMergeEngine, MergeArgv

logger = logging.getLogger(__name__)

_merge_files_list_supported: Optional[bool] = None


class StartLogScanMergeEngine(DedupMergeEngine):
    """控制面 B5：``STP_BACKEND_DEDUP_SCAN_*`` → ``start_log_scan.py -merge_files_list``。"""

    def supports_merge_files_list(self, tool: Dict[str, str]) -> bool:
        global _merge_files_list_supported
        if _merge_files_list_supported is not None:
            return _merge_files_list_supported

        script = Path(tool["script"])
        if not script.is_file():
            _merge_files_list_supported = False
            return False

        try:
            proc = subprocess.run(
                [tool["python"], str(script), "-h"],
                cwd=str(script.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            help_text = (proc.stdout or "") + (proc.stderr or "")
            _merge_files_list_supported = "merge_files_list" in help_text
        except Exception:
            logger.warning(
                "merge_files_list_probe_failed script=%s", script, exc_info=True
            )
            _merge_files_list_supported = False

        logger.info(
            "merge_files_list_supported=%s script=%s",
            _merge_files_list_supported,
            script,
        )
        return _merge_files_list_supported

    def build_merge_argv(
        self,
        tool: Dict[str, str],
        org_files: List[str],
        side_argv: List[str],
    ) -> MergeArgv:
        """构造 ``-merge_files_list`` argv。

        能力门禁（#291）在 ``dedup_scan.build_merge_argv`` 门面执行，以便既有
        测试仍可 patch ``scan_tool_supports_merge_files_list``；本方法只写 listfile。
        """
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".txt",
            prefix="merge_list_",
            dir=str(Path(tempfile.gettempdir())),
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write("\n".join(org_files))
            listfile = Path(f.name)
        argv = [
            tool["python"],
            tool["script"],
            "-merge_files_list",
            str(listfile),
        ] + side_argv
        return MergeArgv(argv=argv, listfile=listfile)


_ENGINE = StartLogScanMergeEngine()


def get_control_plane_merge_engine(platform: str | None = None) -> DedupMergeEngine:
    """解析控制面 B5 merge 引擎。

    ``platform`` 保留给未来选项 B；选项 A / D3 下 mtk 与 unisoc 返回同一实例。
    """
    del platform  # D3：两平台同一工具，无分支
    return _ENGINE


def reset_merge_capability_cache_for_tests() -> None:
    """测试专用：清 ``-merge_files_list`` 探测缓存。"""
    global _merge_files_list_supported
    _merge_files_list_supported = None
