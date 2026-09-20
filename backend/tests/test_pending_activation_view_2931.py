"""#2931 待激活视图：磁盘 head 版本 vs script 表的三态对账（纯函数 + 目录枚举）。

锁四件事：① unregistered/inactive 都进落后集合；② 每族只看磁盘 head（族内旧版
的注册状态属 #735 退役面，不进本视图——存量零引用 backlog 会把它淹掉）；
③ active head 不列出（空视图=全部生效，与 script-versioning 收尾判据同构）；
④ version_key 数值序（1.0.10 > 1.0.9，字典序在此必错）。
"""
from __future__ import annotations

from pathlib import Path

from backend.scripts.check_unreferenced_script_versions import (
    pending_activation_view,
    scan_disk_script_versions,
)


def _mk(root: Path, name: str, *versions: str) -> None:
    for v in versions:
        (root / name / f"v{v}").mkdir(parents=True)


def test_scan_disk_versions_skips_junk_and_reads_names(tmp_path):
    _mk(tmp_path, "gpu_finish", "1.0.5", "1.0.6")
    _mk(tmp_path, "_scratch", "0.1")          # 下划线族：扫描器不认（同 _pick_entry 约定）
    (tmp_path / "loose_file.txt").write_text("x")
    (tmp_path / "no_version_dirs" / "random").mkdir(parents=True)
    out = scan_disk_script_versions(tmp_path)
    assert out == {"gpu_finish": ["1.0.5", "1.0.6"]}  # sorted：视图输出稳定


def test_view_reports_only_head_state_per_family():
    disk = {"check_device": ["1.0.0", "1.0.1"], "old_tool": ["2.0.0"]}
    rows = [
        {"name": "check_device", "version": "1.0.0", "is_active": True},   # head 1.0.1 无行
        {"name": "old_tool", "version": "2.0.0", "is_active": False},      # head inactive
    ]
    lag = pending_activation_view(disk, rows)
    assert lag == [
        {"name": "check_device", "head_version": "1.0.1", "state": "unregistered"},
        {"name": "old_tool", "head_version": "2.0.0", "state": "inactive"},
    ]


def test_activated_head_not_listed_and_numeric_ordering():
    disk = {"monkey_setup": ["2.3.10", "2.3.8", "2.3.9"]}  # head=2.3.10（字典序会选 2.3.9）
    rows = [
        {"name": "monkey_setup", "version": "2.3.8", "is_active": True},
        {"name": "monkey_setup", "version": "2.3.9", "is_active": True},
        {"name": "monkey_setup", "version": "2.3.10", "is_active": True},
    ]
    assert pending_activation_view(disk, rows) == []


def test_name_filter_narrows_output():
    disk = {"a": ["1.0.0"], "b": ["1.0.0"]}
    lag = pending_activation_view(disk, [], name_filter="b")
    assert lag == [{"name": "b", "head_version": "1.0.0", "state": "unregistered"}]
