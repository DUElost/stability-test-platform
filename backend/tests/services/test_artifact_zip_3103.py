"""#3103：目录 → zip 的三道护栏（体积上限 / symlink 判读合一 / 成员名净化）。

对应 issue #3103（origin #3013 已 CLOSED）：
- 原实现无体积上限且是「先 rglob 查链接、再按名打开写」⇒ check-then-use；
- 成员名直接取磁盘名，反斜杠会让把 ``\\`` 当分隔符的解压器越界写。
"""

from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.services import artifact_zip
from backend.services.artifact_zip import (
    ArchiveTooLargeError,
    build_directory_zip_response,
    MAX_ARCHIVE_BYTES,
)


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "ev"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_text("alpha", encoding="utf-8")
    (root / "sub" / "b.txt").write_text("beta", encoding="utf-8")
    return root


def test_directory_zip_lists_and_reads_members(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    resp = build_directory_zip_response(root, download_name="ev")
    assert resp.media_type == "application/zip"
    assert resp.filename == "ev.zip"
    with zipfile.ZipFile(resp.path) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "sub/b.txt"]
        assert zf.read("sub/b.txt") == b"beta"
    Path(resp.path).unlink(missing_ok=True)


def test_empty_directory_still_yields_a_zip(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    resp = build_directory_zip_response(root, download_name="ev")
    with zipfile.ZipFile(resp.path) as zf:
        assert zf.namelist() == []
    Path(resp.path).unlink(missing_ok=True)


def test_symlink_in_tree_is_rejected_400(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "link.txt").symlink_to("/etc/hostname")
    with pytest.raises(HTTPException) as ctx:
        build_directory_zip_response(root, download_name="ev")
    assert ctx.value.status_code == 400


def test_symlinked_directory_is_rejected_400(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / "dirlink").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(HTTPException) as ctx:
        build_directory_zip_response(root, download_name="ev")
    assert ctx.value.status_code == 400


def test_oversize_tree_is_rejected_413_before_compressing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path)
    before = set(Path(tempfile.gettempdir()).glob("*.zip"))
    monkeypatch.setattr(artifact_zip, "MAX_ARCHIVE_BYTES", 4)
    with pytest.raises(HTTPException) as ctx:
        build_directory_zip_response(root, download_name="ev")
    assert ctx.value.status_code == 413
    # 超限时不得留下 temp 归档（先量后压）
    assert set(Path(tempfile.gettempdir()).glob("*.zip")) == before


def test_max_archive_bytes_is_a_sane_positive_cap() -> None:
    assert MAX_ARCHIVE_BYTES >= 64 * 1024 * 1024


def test_backslash_in_member_name_is_rejected(tmp_path: Path) -> None:
    """成员名含 ``\\`` 会被 Windows 解压器当分隔符 ⇒ 越界写。"""
    root = tmp_path / "ev"
    root.mkdir()
    weird = root / "..\\..\\evil.txt"
    weird.write_text("x", encoding="utf-8")
    with pytest.raises(HTTPException) as ctx:
        build_directory_zip_response(root, download_name="ev")
    assert ctx.value.status_code == 400


def test_copy_into_zip_refuses_symlink_swapped_after_check(tmp_path: Path) -> None:
    """判定与读取是同一个 ``O_NOFOLLOW`` 打开：链接换入即 ``ELOOP``。"""
    target = tmp_path / "real.txt"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    with zipfile.ZipFile(tmp_path / "out.zip", "w") as zf:
        with pytest.raises(artifact_zip.ArtifactPathOutsideRootError):
            artifact_zip._copy_into_zip(zf, link, "link.txt")


def test_iter_entries_reports_size_cap_error(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path)
    monkeypatch.setattr(artifact_zip, "MAX_ARCHIVE_BYTES", 1)
    with pytest.raises(ArchiveTooLargeError):
        list(artifact_zip._iter_entries(root))


def test_no_symlink_followed_for_regular_file(tmp_path: Path) -> None:
    """正常文件走 O_NOFOLLOW 仍能读完（护栏不能把正常路径一起拒掉）。"""
    root = _tree(tmp_path)
    with zipfile.ZipFile(tmp_path / "ok.zip", "w") as zf:
        artifact_zip._copy_into_zip(zf, root / "a.txt", "a.txt")
    with zipfile.ZipFile(tmp_path / "ok.zip") as zf:
        assert zf.read("a.txt") == b"alpha"
    assert os.path.isdir(root)
