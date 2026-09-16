"""alembic revision 不可变门禁的行为测试（#2258）。

在临时 git 仓库里**真实构造**「改写已合入 revision」与「附重放迁移」两种历史，
而不是断言工具源码里的字符串——门禁值钱的是它会不会真的拦下 2026-09-13 rechain
窗口那类操作（已合入 main 的 revision 被改写 `down_revision`）。
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE = REPO_ROOT / "tools/dev/check_alembic_revision_immutability.py"
REV_ROOT = "backend/alembic/versions"

_REV_TEMPLATE = '''"""rev {rid}"""

revision = "{rid}"
down_revision = {down}


def upgrade() -> None:
    pass
'''


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return proc.stdout


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """最小仓库：main 上已有两个 revision（aaa → bbb）。"""
    repo = tmp_path / "repo"
    (repo / REV_ROOT).mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")

    (repo / REV_ROOT / "aaa111_base.py").write_text(
        _REV_TEMPLATE.format(rid="aaa111", down="None"), encoding="utf-8"
    )
    (repo / REV_ROOT / "bbb222_child.py").write_text(
        _REV_TEMPLATE.format(rid="bbb222", down='"aaa111"'), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed revisions")
    return repo


def test_self_test_passes():
    result = _run(REPO_ROOT, "--self-test")
    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_blocks_rewriting_merged_revision(repo: Path):
    """复现 rechain 形态：改写已合入 revision 的 down_revision。"""
    target = repo / REV_ROOT / "bbb222_child.py"
    target.write_text(
        _REV_TEMPLATE.format(rid="bbb222", down='"zzz999"'), encoding="utf-8"
    )
    _git(repo, "commit", "-qam", "rechain: reparent bbb222")

    result = _run(repo, "--base", "main~1")

    assert result.returncode == 1
    assert "bbb222_child.py" in result.stdout + result.stderr


def test_gate_allows_rewrite_with_replay_migration(repo: Path):
    """改写 + 同 PR 附重放迁移（down_revision 指向被改写者）→ 放行并留痕。"""
    target = repo / REV_ROOT / "bbb222_child.py"
    target.write_text(
        _REV_TEMPLATE.format(rid="bbb222", down='"zzz999"'), encoding="utf-8"
    )
    (repo / REV_ROOT / "ccc333_replay.py").write_text(
        _REV_TEMPLATE.format(rid="ccc333", down='"bbb222"'), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "rechain + replay")

    result = _run(repo, "--base", "main~1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NOTICE" in result.stdout


def test_gate_blocks_deleting_merged_revision(repo: Path):
    """删除已合入 revision：重放迁移补不了存在性 → 不豁免。"""
    (repo / REV_ROOT / "bbb222_child.py").unlink()
    (repo / REV_ROOT / "ccc333_replay.py").write_text(
        _REV_TEMPLATE.format(rid="ccc333", down='"bbb222"'), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "delete + replay")

    result = _run(repo, "--base", "main~1")

    assert result.returncode == 1


def test_gate_allows_appending_new_revision(repo: Path):
    """正常新增 revision（新链库会执行它）→ 绿。"""
    (repo / REV_ROOT / "ddd444_new.py").write_text(
        _REV_TEMPLATE.format(rid="ddd444", down='"bbb222"'), encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add revision")

    result = _run(repo, "--base", "main~1")

    assert result.returncode == 0, result.stdout + result.stderr


def test_gate_ignores_changes_outside_versions(repo: Path):
    """非 revision 文件不参与判定。"""
    (repo / "backend").mkdir(exist_ok=True)
    (repo / "backend" / "scheduler.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "touch unrelated")

    result = _run(repo, "--base", "main~1")

    assert result.returncode == 0, result.stdout + result.stderr
