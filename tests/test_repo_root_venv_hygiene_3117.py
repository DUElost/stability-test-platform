"""#3117：仓库根不得跟踪 `venv` 条目；`venv` 的 ignore 规则必须覆盖**非目录**形态。

被修的失效：`.gitignore` 里写的是 `venv/`——**尾斜杠只匹配目录**。于是并行 worktree 里
一个 `venv` **symlink**（指向主树的 venv，这是本机常见做法）不被忽略，配合 `git add -A`
就被提交进 main（`ce3a0612`，PR #3114 的第二个 commit）。后果不是"多一个文件"那么轻：
生产工作树该路径是**真目录**，`git pull` 要落这个 tracked 条目会撞位失败 ⇒ 部署被挡。

两条判据分开钉，缺一条就守不住：

① 树的形态：`HEAD` 里不存在 `venv` 条目；
② 规则的行为：在**真造了一个 symlink** 的临时仓库里 `git check-ignore venv` 必须命中
   ——只断言 `.gitignore` 的模式文本会让「模式写对但写在不生效的位置」逃逸。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


def test_repo_tree_tracks_no_root_venv_entry() -> None:
    """① 仓库树里不得有 `venv` 条目（目录、文件、symlink 一视同仁）。"""
    proc = _git(REPO, "ls-tree", "-r", "--name-only", "HEAD", "--", "venv")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", (
        "仓库树里出现了 venv 条目——若这是并行 worktree 的软链被 add -A 收进来的，"
        "删除它并检查 .gitignore 是否又用回了尾斜杠形态（#3117）"
    )


def test_gitignore_ignores_symlink_named_venv(tmp_path: Path) -> None:
    """② 真造一个 `venv` symlink，ignore 规则必须命中（当前修复前这里会返回 1）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copy(REPO / ".gitignore", repo / ".gitignore")
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0

    (repo / "venv").symlink_to("/opt/some-machine/venv")   # 悬空，正好模拟被提交的那条
    assert (repo / "venv").is_symlink(), "语料没造出来 ⇒ 断言会空转"

    proc = _git(repo, "check-ignore", "-v", "venv")
    assert proc.returncode == 0, (
        f"symlink 形态的 venv 未被忽略（rc={proc.returncode}）：{proc.stdout}{proc.stderr}"
    )
    assert "venv" in proc.stdout

    # 反向自证：改动不许把原来的目录覆盖弄丢
    (repo / "sub").mkdir()
    (repo / "sub" / "venv").mkdir()
    assert _git(repo, "check-ignore", "-q", "sub/venv").returncode == 0, (
        "目录形态的 venv 不再被忽略——把尾斜杠换成无斜杠时弄窄了覆盖"
    )
