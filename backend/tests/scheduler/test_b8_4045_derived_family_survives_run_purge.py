"""B8 U5（#4045 第 1 刀）：补跑派生族对「按 run 的无条件清理」必须免疫，且容量按独立单元可见。

Owner 在 #3800 §2.1 第 3 条给的红线是文档话；本单把它钉成三条可判别的实测：

1. **run 清理后 ``compat_reruns/**`` 仍在**，逐文件字节不变——run 族与 job 目录该删的都删了，
   证明不是「整件事没跑」；
2. **判别力**：把 ``compat_reruns`` 误并进清理清单，同一操作立刻把它删掉。红线来自族清单，
   不来自巧合（这条即变异自证，去掉正本归属即失去意义）；
3. **容量口径**：派生族按 ``run/platform/r{id}`` 单列 ``derived_family_usage``，
   **不进** ``run_counts``——否则 E-2 与「应已清理 run 清单」对账时会产出既非漏删也非
   干净的第三种读数（#2188 在 ``jobs`` 上踩过同一个坑）。

只碰临时文件根，不建表、不连业务库。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.scheduler import cron_scheduler
from backend.scripts import measure_center_storage as mcs
from backend.storage_families import (
    COMPAT_RERUNS_FAMILY,
    JOBS_FAMILY,
    RUN_FAMILIES,
)

RUN_ID = 4211


@pytest.fixture
def roots(monkeypatch, tmp_path):
    """日志根 / 控制面根两个写入面（与 B8 G0-a 同一姿势）。"""
    paths = {"log": tmp_path / "log", "control": tmp_path / "control"}
    for key, name in (
        ("STP_AEE_NFS_ROOT", "log"),
        ("STP_CONTROL_PLANE_STORAGE_ROOT", "control"),
    ):
        monkeypatch.setenv(key, str(paths[name]))
        paths[name].mkdir()
    return SimpleNamespace(**paths)


def _write(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original")
    return path


def _seed(roots):
    """一笔补跑的全部落点：run 族产物 + 派生族副本 + job 目录。"""
    run_family_files = {
        family: _write(roots.log / family / str(RUN_ID) / "payload.txt")
        for family in RUN_FAMILIES
    }
    rerun_leaf = Path(COMPAT_RERUNS_FAMILY) / str(RUN_ID) / "mtk" / "r7"
    copied_event = _write(roots.log / rerun_leaf / "events/col/SN/aee_exp/e/main.dbg")
    xls = _write(roots.log / rerun_leaf / "Result_MergeFiles_Compat.xls")
    job_file = _write(roots.control / JOBS_FAMILY / "77" / "log.txt")
    return run_family_files, copied_event, xls, job_file


def test_run_purge_removes_run_families_but_not_the_derived_family(roots):
    """#1：按 run 的清理照删 run 族与 job，但不得触及 compat_reruns/。"""
    run_family_files, copied_event, xls, job_file = _seed(roots)

    assert cron_scheduler.purge_run_storage_dirs([RUN_ID], {RUN_ID: [77]}) == set()

    assert all(not path.exists() for path in run_family_files.values())
    assert not job_file.exists()
    # 派生族整棵子树原样存活（字节不变，不是「被移走又建回来」）。
    assert copied_event.read_text() == "original"
    assert xls.read_text() == "original"


def test_guard_is_the_family_list_not_luck(roots, monkeypatch):
    """#2 判别力：把该族误并进 RUN_FAMILIES，同一操作立刻删掉它。"""
    _run_family_files, copied_event, _xls, _job = _seed(roots)
    monkeypatch.setattr(
        cron_scheduler, "RUN_FAMILIES",
        tuple(cron_scheduler.RUN_FAMILIES) + (COMPAT_RERUNS_FAMILY,),
    )

    assert cron_scheduler.purge_run_storage_dirs([RUN_ID], {RUN_ID: [77]}) == set()
    assert not copied_event.exists()


def test_derived_capacity_is_unit_keyed_and_absent_from_run_counts(roots):
    """#3：容量按 ``run/platform/r{id}`` 单列，且不进 run 分解。"""
    _files, _copied, _xls, _job = _seed(roots)
    # 同 run 的第二笔补跑、以及一个非登记形态的目录（必须不被当成单元）。
    _write(roots.log / COMPAT_RERUNS_FAMILY / str(RUN_ID) / "unisoc" / "r8" / "x.txt")
    _write(roots.log / COMPAT_RERUNS_FAMILY / str(RUN_ID) / "mtk" / "scratch" / "y.txt")
    _write(roots.log / COMPAT_RERUNS_FAMILY / ".tmp-sweep" / "z.txt")

    usage = mcs.collect_derived_usage(roots.log)["compat_reruns"]
    assert usage["independent_units"] == 2
    assert usage["distinct_runs"] == 1
    assert {row["name"] for row in usage["top_units"]} == {
        f"{RUN_ID}/mtk/r7", f"{RUN_ID}/unisoc/r8",
    }
    assert usage["bytes"] == 2 * len("original") + len("original")  # r7 两文件 + r8 一文件
    assert COMPAT_RERUNS_FAMILY not in mcs.collect_run_usage(roots.log)
    assert COMPAT_RERUNS_FAMILY not in mcs.collect_baseline(
        roots.log, control_plane_root=roots.control, merge_result_root=None, top=5,
    )["run_counts"]
