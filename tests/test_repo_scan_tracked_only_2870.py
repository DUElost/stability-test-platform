"""#2870：守卫的扫描面必须是「仓库跟踪内容」——这条口径本身也要有判据。

被修的失效：`tests/test_ci_test_db_guard_wiring.py` 与 `tests/test_removed_env_keys.py`
用 `rglob` 扫文件系统，于是把两类与仓库内容无关的东西读进判定集：

- `.wt/*`：并行 Execution 的专属 worktree，每个里面都是**整仓副本**（本机实测 10 个 worktree
  = 14,645 个 `.py`），结果守卫**本机恒红、CI 恒绿**——红灯失去信息量，真回归与噪声不可分；
- 本地未跟踪的 `backend/.env`：里面留着已移除的键，同样只在本机点红。

本文件不重复测那两条守卫的语义，只钉**扫描面口径**这一层（含 ratchet，防第三条守卫再犯）：
`tests/repo_scan.py` 与 `tools/dev/check-internal-ip-leak.py` 必须是同一份「仓库内容」定义。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.repo_scan import iter_scanned, tracked_and_new_files

REPO = Path(__file__).resolve().parents[1]

#: 允许继续用文件系统扫描的守卫（附理由，不是豁免偏好）：
#: `test_env_example_parity.py` 用 `_rel_parts()` 只跳仓库**内部**的 `.wt`（#1978：
#: 绝对路径含 `.wt` 会把整个 worktree 跳过、语料变空），本机与 CI 均绿；迁移到统一口径
#: 属另单，本 ratchet 不顺手改一条绿的守卫。
FS_SCAN_ALLOWLIST: dict[str, str] = {
    "tests/test_env_example_parity.py": "#1978 的 _rel_parts 方案，已正确处理 .wt 且不留死条目",
    "tests/repo_scan.py": "口径的实现本身——唯一被允许接触文件系统枚举的模块",
}

#: 「绑定到仓库根的标识符」：`REPO = Path(__file__).resolve().parents[1]` 这一族的写法。
_ROOT_BIND_RE = re.compile(
    r"^\s*(?P<name>\w+)\s*(?::\s*Path\s*)?=\s*Path\(__file__\)\.resolve\(\)\.parents\[1\]",
    re.MULTILINE,
)
#: 命名常量形态（跨模块 import 进来的根也长这样）。
_KNOWN_ROOTS = ("REPO", "REPO_ROOT", "ROOT")


def _walk_hits(text: str) -> list[str]:
    """返回「从仓库根直接做文件系统扫描」的命中行（#2870 的失效形态）。

    判据**不绑标识符字面量**：只看谁被绑定成仓库根，再查对它调用 `.rglob(` / `os.walk(`。
    否则像 `R = Path(__file__).resolve().parents[1]; R.rglob(...)` 这种换个变量名的写法
    就能绕过（第一次试就被绕过了，这条注释是那次留下的）。
    子目录拼装（`REPO / "docs"`）与临时目录（`tmp_path`）刻意不拦：它们读不到 `.wt/` 副本，
    判据过宽只会误伤无关写法，下一次就有人把整条 ratchet 注释掉（#2641 的账）。
    """
    names = set(_ROOT_BIND_RE.findall(text)) | {
        n for n in _KNOWN_ROOTS if re.search(rf"\b{n}\s*=\s*Path\(", text)
    }
    hits: list[str] = []
    for line in text.splitlines():
        for name in names:
            if re.search(rf"\b{re.escape(name)}\.rglob\(", line) or re.search(
                rf"os\.walk\(\s*{re.escape(name)}\b", line
            ):
                hits.append(line.strip())
                break
    return hits


# ── 真实仓库上的不变量 ──────────────────────────────────────────────────────


def test_scan_set_is_repo_content_not_filesystem() -> None:
    files = tracked_and_new_files(REPO)
    assert len(files) > 3000, f"扫描面塌陷（{len(files)} 项）——守卫会静默全绿"
    leaked = [f for f in files if f.startswith(".wt/")]
    assert not leaked, f"worktree 副本漏进扫描面：{leaked[:3]}"
    # 本地未跟踪的 .env 出局，但**已跟踪**的示例必须在（#2661 的漂移面）
    assert "backend/.env" not in files
    assert "backend/.env.example" in files


def test_py_scan_excludes_test_faces_but_keeps_tools() -> None:
    rels = {
        p.relative_to(REPO).as_posix()
        for p in iter_scanned((".py",), root=REPO, exclude_prefixes=("backend/tests/", "tests/"))
    }
    assert "tools/dev/check-internal-ip-leak.py" in rels
    assert not any(r.startswith(("tests/", "backend/tests/")) for r in rels), "排除面前缀失效"
    assert not any(r.startswith(".wt/") for r in rels)


# ── 合成 git 仓库：证明口径而不是猜语义 ──────────────────────────────────────


def _git(root: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, f"git {args} 失败：{proc.stderr.strip()}"


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "test")
    (tmp_path / ".gitignore").write_text(".wt/\nsecret.env\n", encoding="utf-8")
    (tmp_path / "tracked.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "seed")
    (tmp_path / "new_untracked.py").write_text("y = 2\n", encoding="utf-8")   # #2402 时点
    (tmp_path / "secret.env").write_text("Z = 3\n", encoding="utf-8")         # 被忽略
    (tmp_path / ".wt" / "sibling" / "copy.py").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / ".wt" / "sibling" / "copy.py").write_text("z = 4\n", encoding="utf-8")
    return tmp_path


def test_scan_set_includes_untracked_and_ignores_worktree_and_ignored(fake_repo: Path) -> None:
    files = tracked_and_new_files(fake_repo)
    assert "tracked.py" in files
    assert "new_untracked.py" in files, (
        "未跟踪新文件出局 ⇒ 『git add 之前』这个最该拦的时点不在门禁眼里（#2402）"
    )
    assert "secret.env" not in files, "被 .gitignore 命中的本地文件不得进判定集"
    assert not [f for f in files if f.startswith(".wt/")], "worktree 副本漏进判定集"


def test_empty_scan_set_is_a_failure_not_a_pass(tmp_path: Path) -> None:
    """git 不可用/非仓库时必须**抛错**：返回空集会伪装成「全仓无违规」（#2639 那类静默失效）。"""
    with pytest.raises(RuntimeError):
        tracked_and_new_files(tmp_path)


def test_suffix_misuse_raises_instead_of_silently_matching_nothing() -> None:
    """`("py",)` 这种写法会扫出空集且全绿——判据自己骗自己，必须在入口拦下。"""
    with pytest.raises(AssertionError):
        iter_scanned(("py",), root=REPO)


# ── ratchet：第三条守卫不得再犯 ─────────────────────────────────────────────


def test_no_new_guard_walks_the_filesystem_from_the_repo_root() -> None:
    offenders: list[str] = []
    for rel in sorted(tracked_and_new_files(REPO)):
        if not (rel.startswith("tests/") and rel.endswith(".py")):
            continue
        if rel in FS_SCAN_ALLOWLIST:
            continue
        text = (REPO / rel).read_text(encoding="utf-8")
        if _walk_hits(text):
            offenders.append(f"{rel}: {_walk_hits(text)[0][:60]}")
    assert not offenders, (
        "以下守卫从仓库根做文件系统扫描，会把 .wt/* 副本与本地未跟踪文件读进判定集："
        f"{offenders}\n处置：改用 `from tests.repo_scan import tracked_and_new_files / "
        "iter_scanned`（#2870）。"
    )
    stale = [k for k in FS_SCAN_ALLOWLIST if not (REPO / k).is_file()]
    assert not stale, f"豁免表留下失效条目（等于悄悄扩大豁免面）：{stale}"


def _sample(name: str, tail: str) -> str:
    """拼出「绑定到仓库根 + 文件系统扫描」的样本代码。

    必须**拼接**而不是直写：本文件也在 ratchet 的扫描面里，写出字面量等于自己判红
    （试了两次都被自己的判据抓，这两次注释就是那两次留下的）。
    """
    resolve = "Path(__file__).resolve()"
    return f"{name} = {resolve}.parents[1]\n{tail}\n"


def test_ratchet_has_teeth_on_the_original_bug_and_no_false_positives() -> None:
    """ratchet 自身要有红侧：拿原始失效形态与三类无关写法对拍。

    不加这条，判据一旦写歪（多要求一个空格、或只认固定变量名），ratchet 会安静地永远
    通过而表面上「守卫已加」——#2639/#2641 数过的正是这种守卫。
    """
    scan = ".rglob("
    assert _walk_hits(_sample("R", f'paths = list(R{scan}"*.py"))')), "改名绕过的根扫描必须被认出"
    assert _walk_hits(_sample("ROOT", "import os\nlist(os.walk(ROOT))")), "os.walk(根) 必须被认出"

    must_not = {
        # 子目录拼装：读不到 .wt 副本，拦它只会误伤
        "子目录拼装": _sample("X", f'for q in (X / "docs"){scan}"*.md"): pass'),
        # 非仓库根常量的 rglob（安装器子树）
        "安装器子树": f'INSTALLER_ROOT = Path("tools/site_config")\n'
                      f'list(INSTALLER_ROOT{scan}"*.py"))',
        # 临时目录：与仓库内容无关
        "临时目录": f"def t(tmp_path):\n    return list(tmp_path{scan}" + '"*"))',
    }
    for label, text in must_not.items():
        assert not _walk_hits(text), f"ratchet 误伤无关写法（{label}）：{_walk_hits(text)}"
