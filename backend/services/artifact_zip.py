"""Directory → zip helpers for PlanRun / DLE download surfaces (#3013).

Used when the on-disk fact is a tree (``devices/...`` event dirs,
``jira/{plan_run_id}/`` extract bundles) but the UI needs a single downloadable
blob.

Three guards, all enforced in a **single** traversal (#3103):

- symlinks are refused, and the refusal is the read: every file is opened with
  ``O_NOFOLLOW`` and copied through that fd. The previous shape ran a separate
  ``rglob`` check and then ``ZipFile.write``, which re-opens *by name* — a link
  created after the check was still followed (check-then-use).
- the uncompressed total is summed with an early abort (``MAX_ARCHIVE_BYTES``),
  so one request cannot fill the control-plane disk with a temp copy.
- member names are sanitised: no backslashes, no ``..`` segments, so the archive
  cannot carry an entry that escapes on extract on another platform.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.core.artifact_paths import ArtifactPathOutsideRootError

#: 单次目录归档的**未压缩**体积上限。下载端点是同步 `def`（anyio 线程池默认 40），
#: 每个请求都会在控制面 ``/tmp`` 落一份完整压缩副本；没有上限时并发拉取即可同时
#: 占满线程并填盘（#3103）。超限直接 413，不进入压缩。
#:
#: 为什么是常量而不是 env 旋钮：本仓 env 键要过 `env_inventory` /
#: `test_env_example_parity` 两道台账（新增键需同步登记面与计数）。上限只是
#: 防呆护栏，先按常量落地；若运维需要按部署调，再走一次台账登记。
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024

#: 单个成员名里不允许出现的分隔符（POSIX 下 zip 成员名不会转换反斜杠，
#: 把 ``\`` 当分隔符的解压器会因此越界写）。
_FORBIDDEN_NAME_CHARS = ("\\",)


class ArchiveTooLargeError(Exception):
    """目录归档超过 ``MAX_ARCHIVE_BYTES``。"""


def _validate_arcname(arcname: str) -> None:
    if any(ch in arcname for ch in _FORBIDDEN_NAME_CHARS):
        raise ArtifactPathOutsideRootError(
            f"archive member name contains a path separator: {arcname!r}"
        )
    parts = arcname.split("/")
    if any(part in ("..", "") for part in parts):
        raise ArtifactPathOutsideRootError(
            f"archive member name is not a plain relative path: {arcname!r}"
        )


def _iter_entries(root: Path) -> Iterator[tuple[Path, str]]:
    """单趟列出 (真实文件, 归档成员名)，超限即抛、遇链接即拒。

    ``os.walk(followlinks=False)`` 不进入被链接的目录，但链接**目录**仍会出现在
    ``dirnames`` 里，故两层都要显式判。
    """
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in list(dirnames):
            candidate = Path(dirpath) / name
            if candidate.is_symlink():
                raise ArtifactPathOutsideRootError(
                    f"symlink not allowed under download tree: {candidate}"
                )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ArtifactPathOutsideRootError(
                    f"symlink not allowed under download tree: {full}"
                )
            try:
                st = full.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            total += st.st_size
            if total > MAX_ARCHIVE_BYTES:
                raise ArchiveTooLargeError(
                    f"directory exceeds {MAX_ARCHIVE_BYTES} bytes: {root}"
                )
            arcname = full.relative_to(root).as_posix()
            _validate_arcname(arcname)
            yield full, arcname


def _copy_into_zip(zf: zipfile.ZipFile, full: Path, arcname: str) -> None:
    """用 ``O_NOFOLLOW`` 打开后从该 fd 读取——判定与读取是同一个操作。"""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(full, flags)
    except OSError as exc:  # ELOOP：检查之后被换成了符号链接
        raise ArtifactPathOutsideRootError(
            f"refusing to archive {full}: {exc}"
        ) from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ArtifactPathOutsideRootError(f"not a regular file: {full}")
        info = zipfile.ZipInfo(arcname, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (st.st_mode & 0xFFFF) << 16
        with zf.open(info, "w") as dest, os.fdopen(os.dup(fd), "rb", closefd=True) as src:
            shutil.copyfileobj(src, dest, length=1024 * 1024)
    finally:
        os.close(fd)


def _unlink_quiet(path: Path) -> None:
    path.unlink(missing_ok=True)


def build_directory_zip_response(
    directory: Path,
    *,
    download_name: str,
) -> FileResponse:
    """Zip *directory* to a temp file and return a ``FileResponse``.

    Caller must have already validated that *directory* is under the shared
    storage root and is a real directory (not a symlink escape).
    """
    if not directory.is_dir() or directory.is_symlink():
        raise HTTPException(status_code=404, detail="directory not found")

    # 先量后压：超限不产生 temp 文件，也不进入 DEFLATE（#3103）。
    try:
        entries = list(_iter_entries(directory))
    except ArchiveTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ArtifactPathOutsideRootError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # NamedTemporaryFile deletes on close by default; keep until response drains.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for full, arcname in entries:
                _copy_into_zip(zf, full, arcname)
    except ArtifactPathOutsideRootError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    name = download_name if download_name.endswith(".zip") else f"{download_name}.zip"
    return FileResponse(
        path=str(tmp_path),
        filename=name,
        media_type="application/zip",
        background=BackgroundTask(_unlink_quiet, tmp_path),
    )
