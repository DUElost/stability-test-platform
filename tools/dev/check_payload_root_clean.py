#!/usr/bin/env python3
"""Assert the hot-update payload root carries no untracked file (#3112).

The hot-update tarball and the ``desired`` artifact digest share one enumeration
(``backend/services/host_updater.py::_iter_payload_files`` /
``backend/agent/contracts/artifact_digest.py::collect_artifact_entries``; "digest 输入集 =
部署输入集" 由同一份代码保证，ADR-0040 D1), and that enumeration walks the
**working tree** rooted at ``backend/agent/``. Two consequences make an untracked
file there different from an untracked file anywhere else:

1. it is payload content — it ships to every host on the next hot-update (and
   ``rsync --delete`` keeps it there);
2. it moves ``desired digest``, which is the **sole** judge of
   ``agent_code_sync_status`` (ADR-0040 v1.1, ``backend/services/agent_version_info.py``)
   — so one stray file flips the whole fleet to ``drift``, and a real drift is
   indistinguishable from that noise on the same badge.

``check-deploy-source.sh`` hard-fails on a dirty **tracked** worktree but only
warns about untracked files ("可能是并发会话的临时文件"). That tolerance is right
for the repo root (the systemd ``WorkingDirectory`` is shared with parallel
sessions) and wrong for the payload root, because of the two consequences above.

``.gitignore``d paths are unaffected: git does not report ignored files as
untracked, which is what keeps ``backend/agent/resources/`` (gitignore:89) and
``__pycache__`` from tripping this check.

Usage (from the repo root, or anywhere inside it)::

    ./venv/bin/python tools/dev/check_payload_root_clean.py

Exit codes::

    0 = payload root is clean (no non-ignored untracked file)
    1 = untracked file(s) found, or the state could not be determined
        (fail closed: an unverifiable payload root must not be deployed)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

#: 与 ``host_updater._AGENT_SOURCE_DIR`` 同源（载荷打包/摘要枚举的根）。
DEFAULT_PAYLOAD_ROOT = "backend/agent"

_REMEDY = (
    "  处置：归位（git add → 走 PR）／删除／写进 .gitignore——"
    "载荷根只允许「已评审的已提交内容」"
)


def _repo_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check_payload_root_clean: FAIL —— 无法执行 git（{exc}）；载荷根状态未知即视为未通过",
              file=sys.stderr)
        raise SystemExit(1) from None
    if proc.returncode != 0:
        reason = _first_stderr_line(proc, "git rev-parse --show-toplevel")
        print(f"check_payload_root_clean: FAIL —— 无法定位 git 仓库根（{reason}）；"
              "载荷根状态未知即视为未通过", file=sys.stderr)
        raise SystemExit(1)
    return Path(proc.stdout.strip()).resolve()


def _first_stderr_line(proc: subprocess.CompletedProcess, what: str) -> str:
    """git 失败时把它自己的一句话原因透出来（别把 Python 的 Command repr 丢给操作者）。"""
    for line in (proc.stderr or "").splitlines():
        if line.strip():
            return line.strip()
    return f"{what} 退出码 {proc.returncode}"


def untracked_files(repo_root: Path, payload_root: str) -> list[str]:
    """载荷根下**非 ignore** 的未跟踪文件（仓库相对路径，已排序）。

    ``--untracked-files=all`` 让未跟踪目录内的文件逐个列出（而不是只报目录），
    操作者才能直接照着删；``.gitignore`` 命中项 git 本就不报。
    读不到状态即视为未通过（fail closed）——「未知」不能当「干净」用。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain",
             "--untracked-files=all", "--", payload_root],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check_payload_root_clean: FAIL —— 无法执行 git（{exc}）；"
              "载荷根状态未知即视为未通过", file=sys.stderr)
        raise SystemExit(1) from None
    if proc.returncode != 0:
        reason = _first_stderr_line(proc, f"git status -- {payload_root}")
        print(f"check_payload_root_clean: FAIL —— 读取 {payload_root} 的 git 状态失败"
              f"（{reason}）；载荷根状态未知即视为未通过", file=sys.stderr)
        raise SystemExit(1)
    return sorted(
        line[3:].strip()
        for line in proc.stdout.splitlines()
        if line.startswith("??")
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", default=None, help="仓库根（缺省用 git 定位）")
    parser.add_argument(
        "--payload-root",
        default=DEFAULT_PAYLOAD_ROOT,
        help=f"载荷根（仓库相对；缺省 {DEFAULT_PAYLOAD_ROOT}）",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root(args.repo_root)
    offenders = untracked_files(repo_root, args.payload_root)
    if offenders:
        print(
            f"check_payload_root_clean: FAIL —— 载荷根 {args.payload_root}/ 下有 "
            f"{len(offenders)} 个未跟踪文件：这些文件会被热更新推送到全部主机，"
            "并改变 desired digest（agent_code_sync_status 的唯一判据）",
            file=sys.stderr,
        )
        for path in offenders:
            print(f"  ?? {path}", file=sys.stderr)
        print(_REMEDY, file=sys.stderr)
        return 1
    print(f"check_payload_root_clean: OK —— 载荷根 {args.payload_root}/ 无未跟踪文件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
