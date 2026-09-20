"""ADR-0033 Phase 2 选项 A：B5 DedupMergeEngine 行为契约。

验收：两平台同一 ``StartLogScanMergeEngine``；argv 与历史
``build_merge_argv``（``-merge_files_list`` + listfile）等价；不引入 GT。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services import dedup_scan as ds
from backend.services.dedup import (
    StartLogScanMergeEngine,
    get_control_plane_merge_engine,
)
from backend.services.dedup.base import DedupMergeEngine, MergeArgv


@pytest.fixture(autouse=True)
def _reset_merge_probe_cache():
    ds.reset_merge_capability_cache_for_tests()
    yield
    ds.reset_merge_capability_cache_for_tests()


def test_get_control_plane_merge_engine_same_for_both_platforms():
    """选项 A / D3：mtk 与 unisoc 共用同一 B5 引擎实例。"""
    mtk = get_control_plane_merge_engine("mtk")
    unisoc = get_control_plane_merge_engine("unisoc")
    none = get_control_plane_merge_engine(None)
    assert isinstance(mtk, StartLogScanMergeEngine)
    assert isinstance(unisoc, StartLogScanMergeEngine)
    assert mtk is unisoc is none


def test_engine_is_dedup_merge_engine_acl():
    engine = get_control_plane_merge_engine("unisoc")
    assert isinstance(engine, DedupMergeEngine)


def test_engine_build_merge_argv_matches_facade(tmp_path: Path):
    """引擎直接构造的 argv 与 dedup_scan 门面行为等价。"""
    org = [str(tmp_path / "a_org.xls"), str(tmp_path / "b_org.xls")]
    script = tmp_path / "start_log_scan.py"
    script.write_text("# stub\n", encoding="utf-8")
    tool = {"python": "python", "script": str(script)}
    side = ["-side", "shanghai"]

    with patch.object(
        StartLogScanMergeEngine, "supports_merge_files_list", return_value=True
    ):
        via_engine = get_control_plane_merge_engine("unisoc").build_merge_argv(
            tool, org, side
        )
        via_facade_argv, via_facade_list = ds.build_merge_argv(
            tool, org, side, platform="mtk"
        )

    assert isinstance(via_engine, MergeArgv)
    assert via_engine.argv[2] == "-merge_files_list"
    assert via_facade_argv[2] == "-merge_files_list"
    # 两次调用各写一份 listfile；内容必须相同
    assert via_engine.listfile is not None and via_facade_list is not None
    assert via_engine.listfile.read_text(encoding="utf-8").splitlines() == org
    assert via_facade_list.read_text(encoding="utf-8").splitlines() == org
    assert via_engine.argv[:3] == via_facade_argv[:3]
    assert via_engine.argv[4:] == via_facade_argv[4:] == side
    via_engine.listfile.unlink(missing_ok=True)
    via_facade_list.unlink(missing_ok=True)


def test_engine_does_not_wire_scan_result_gt_cli():
    """选项 A：样板不得把 Scan-Result-GT CLI / 私有 env 挂进 B5 argv 路径。"""
    import backend.services.dedup.start_log_scan_merge as mod

    from tools.dev.source_anchor import SourceGuard

    guard = SourceGuard.of_module(mod).anchored(
        '"-merge_files_list"',
        expect=1,
    )
    guard.assert_absent(
        "scan_result.py",
        why="选项 A：B5 引擎不得挂 GT CLI",
    )
    guard.assert_absent(
        "STP_UNISOC_SCAN_RESULT",
        why="选项 A：不得引入 GT 私有 env 键",
    )


def test_build_merge_argv_platform_kwarg_does_not_change_tool(tmp_path: Path):
    org = [str(tmp_path / "a_org.xls")]
    script = tmp_path / "start_log_scan.py"
    script.write_text("# stub\n", encoding="utf-8")
    tool = {"python": "/opt/py", "script": str(script)}

    with patch.object(
        StartLogScanMergeEngine, "supports_merge_files_list", return_value=True
    ):
        argv_mtk, lf_mtk = ds.build_merge_argv(
            tool, org, ["-side", "factory"], platform="mtk"
        )
        argv_uni, lf_uni = ds.build_merge_argv(
            tool, org, ["-side", "factory"], platform="unisoc"
        )

    assert argv_mtk[0] == argv_uni[0] == "/opt/py"
    assert argv_mtk[1] == argv_uni[1] == str(script)
    assert argv_mtk[2] == argv_uni[2] == "-merge_files_list"
    lf_mtk.unlink(missing_ok=True)
    lf_uni.unlink(missing_ok=True)
