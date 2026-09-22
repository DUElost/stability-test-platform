"""#3112：热更新**载荷根**下的未跟踪文件必须硬拦——它是载荷内容，不是临时文件。

被修的失效：热更新 tarball 与 desired digest 共用同一份 `os.walk(backend/agent/)`
枚举（ADR-0040 D1），所以载荷根下的未跟踪文件会 (1) 随下次热更新下发到全部主机、
(2) 改变 desired digest——而 digest 是 `agent_code_sync_status` 的唯一判据（ADR-0040 v1.1）。
实测：在 `backend/agent/` 放一个临时文件即让 48 台主机的徽标集体变 drift，且与真漂移
在同一枚徽标上不可区分。

而部署源守卫此前只把 **tracked** 脏工作区当硬失败，未跟踪文件一律 WARN（「可能是并发
会话的临时文件」）。那条容忍度对仓库根成立（systemd WorkingDirectory 与并行会话共享），
对载荷根不成立——这就是本文件钉住的边界：**载荷根硬拦，之外只 WARN**。

本文件测的是检查器自身的行为（临时 git 仓库，不碰真实工作树）与守卫的接线形态；
载荷枚举/摘要语义不在这里重复测（归 ADR-0040 那批契约测试）。
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "tools" / "dev" / "check_payload_root_clean.py"
GUARD = REPO / "tools" / "dev" / "check-deploy-source.sh"
HOST_UPDATER = REPO / "backend" / "services" / "host_updater.py"
#: 载荷根下被 `.gitignore` 覆盖、因而**不该**被本检查拦下的两处现实路径。
IGNORED_PATHS = ("backend/agent/resources/apk.bin", "backend/agent/__pycache__/m.cpython-313.pyc")


def _load_checker():
    spec = importlib.util.spec_from_file_location("stp_check_payload_root_clean", CHECKER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHECKER_MOD = _load_checker()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    )


@pytest.fixture()
def payload_repo(tmp_path: Path) -> Path:
    """一个最小的「载荷根形状」仓库：一个包 + 一个脚本族 + 被 ignore 的 resources/。"""
    repo = tmp_path / "repo"
    (repo / "backend" / "agent" / "scripts" / "fam" / "v1.0.0").mkdir(parents=True)
    (repo / "backend" / "agent" / "resources").mkdir()
    (repo / "backend" / "agent" / "tracked.py").write_text("print(1)\n", encoding="utf-8")
    (repo / "backend" / "agent" / "scripts" / "fam" / "v1.0.0" / "fam.py").write_text(
        "print(2)\n", encoding="utf-8"
    )
    (repo / ".gitignore").write_text("/backend/agent/resources/\n__pycache__/\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    return repo


def _run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--repo-root", str(repo)],
        capture_output=True, text=True,
    )


# ── 行为：载荷根硬拦 ────────────────────────────────────────────────────────


def test_clean_payload_root_passes(payload_repo: Path) -> None:
    proc = _run(payload_repo)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_untracked_file_under_payload_root_fails_with_path_and_remedy(
    payload_repo: Path,
) -> None:
    offender = payload_repo / "backend" / "agent" / "scratch_probe.txt"
    offender.write_text("probe\n", encoding="utf-8")

    proc = _run(payload_repo)

    assert proc.returncode == 1, proc.stdout
    # 反向自证：git 真的把它当未跟踪（否则这条测试会对着「本来就是干净」的仓库空转）
    status = subprocess.run(
        ["git", "-C", str(payload_repo), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "backend/agent/scratch_probe.txt" in status, "语料没造出来 ⇒ 断言假绿"
    assert "backend/agent/scratch_probe.txt" in proc.stderr
    assert "?? " in proc.stderr
    assert "处置" in proc.stderr, "光说红不够：要给出归位/删除/gitignore 三条出口"


def test_nested_untracked_dir_lists_every_file(payload_repo: Path) -> None:
    """`--untracked-files=all`：操作者要能照着路径直接删，而不是只看到目录名。"""
    nested = payload_repo / "backend" / "agent" / "leftover"
    nested.mkdir()
    (nested / "a.txt").write_text("a\n", encoding="utf-8")
    (nested / "b.txt").write_text("b\n", encoding="utf-8")

    proc = _run(payload_repo)

    assert proc.returncode == 1
    assert "backend/agent/leftover/a.txt" in proc.stderr
    assert "backend/agent/leftover/b.txt" in proc.stderr


def test_untracked_file_outside_payload_root_passes(payload_repo: Path) -> None:
    """载荷根**之外**的未跟踪文件不归本检查管（并发会话的临时文件不该阻塞部署）。"""
    (payload_repo / "rollout-er102.pid").write_text("123\n", encoding="utf-8")

    proc = _run(payload_repo)

    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


def test_gitignored_paths_under_payload_root_are_not_flagged(payload_repo: Path) -> None:
    """`resources/`（大件带外布放，.gitignore:89）与 `__pycache__/` 不得误判。

    这条不变量要是破了，每次部署都会红——守卫随即会被整体注释掉。
    """
    for rel in IGNORED_PATHS:
        path = payload_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n", encoding="utf-8")
    # 反向自证：两处确实存在且确实被 ignore
    for rel in IGNORED_PATHS:
        assert (payload_repo / rel).is_file()
    ignored = subprocess.run(
        ["git", "-C", str(payload_repo), "status", "--porcelain", "--ignored=matching",
         "--untracked-files=all"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert "resources/" in ignored and "__pycache__/" in ignored

    proc = _run(payload_repo)

    assert proc.returncode == 0, proc.stderr


def test_not_a_git_repo_fails_closed(tmp_path: Path) -> None:
    """读不到状态 ≠ 干净：载荷根未知时必须拒绝部署（fail closed）。"""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    proc = _run(plain)

    assert proc.returncode == 1
    assert "FAIL" in proc.stderr
    assert "Traceback" not in proc.stderr, "别把 Python 异常丢给操作者"


def test_real_repo_payload_root_is_clean() -> None:
    """本仓库自身必须过（否则本 PR 一落地就把每次部署卡死）。"""
    proc = _run(REPO)
    assert proc.returncode == 0, proc.stderr


# ── 结构不变量与接线 ────────────────────────────────────────────────────────


def test_default_payload_root_matches_hot_update_source_dir() -> None:
    """检查器的根必须就是热更新枚举的根——两处漂开，本检查就守了个空门。"""
    assert CHECKER_MOD.DEFAULT_PAYLOAD_ROOT == "backend/agent"
    source = HOST_UPDATER.read_text(encoding="utf-8")
    match = re.search(
        r"^_AGENT_SOURCE_DIR\s*=\s*(.+)$", source, re.MULTILINE,
    )
    assert match, "_AGENT_SOURCE_DIR 不见了 ⇒ 载荷根定义已搬家，本检查需重新对齐"
    assert '"agent"' in match.group(1) and "parent.parent" in match.group(1), (
        "_AGENT_SOURCE_DIR 不再是 <repo>/backend/agent："
        f"{match.group(1).strip()} —— 请同步 DEFAULT_PAYLOAD_ROOT"
    )


def test_guard_wires_the_checker_as_a_hard_failure() -> None:
    """接线形态：载荷根检查必须是 `if ! …; then … exit 1; fi`（不是 WARN 家族）。"""
    text = GUARD.read_text(encoding="utf-8")
    invocation = [
        line for line in text.splitlines()
        if "check_payload_root_clean.py" in line
    ]
    assert len(invocation) == 1, f"调用点应恰有一处，实际 {len(invocation)}"
    assert invocation[0].strip().startswith('if ! "$PYTHON"'), invocation[0]
    at = text.index(invocation[0])
    block = text[at:text.index("\nfi\n", at)]  # 本块自身（不含后面的 WARN 块）
    assert "exit 1" in block, "硬失败必须 exit 1——runbook 靠退出码判定「停止部署」"
    assert "|| true" not in block, "不许把失败吞成通过"
    assert "WARN" not in block, "载荷根是硬拦，不是 WARN"
    # 顺序：硬拦在前，仓库根其余未跟踪文件的 WARN 在后（否则会读成「先说不阻塞、后说失败」）
    assert at < text.index("WARN —— 存在未跟踪文件"), "WARN 块跑到了硬拦之前"
