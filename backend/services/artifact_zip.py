"""Directory → zip helpers for PlanRun / DLE download surfaces (#3013).

Used when the on-disk fact is a tree (``devices/...`` event dirs,
``jira/{plan_run_id}/`` extract bundles) but the UI needs a single downloadable
blob. Symlinks under the tree are rejected (same policy as extract copy).
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from backend.core.artifact_paths import ArtifactPathOutsideRootError


def _reject_symlinks_under(root: Path) -> None:
    for entry in root.rglob("*"):
        if entry.is_symlink():
            raise ArtifactPathOutsideRootError(
                f"symlink not allowed under download tree: {entry}"
            )


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
    try:
        _reject_symlinks_under(directory)
    except ArtifactPathOutsideRootError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # NamedTemporaryFile deletes on close by default; keep until response drains.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(directory.rglob("*")):
                if not path.is_file():
                    continue
                arcname = path.relative_to(directory).as_posix()
                zf.write(path, arcname)
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
