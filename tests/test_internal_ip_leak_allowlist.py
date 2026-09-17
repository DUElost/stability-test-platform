"""#1655：device_serial.py 不得再以整文件白名单绕过内网泄漏门禁。

回归背景：`backend/core/device_serial.py` 曾在 ALLOWLIST_PREFIXES 中整文件
放行（#1356），导致该文件此后在 CI / pre-commit 完全不被扫描——将来加入
真实内网地址或序列号也不会被拦。修复：改为 SAFE_TOKENS 逐 token 放行
（``0123456789ABCDEF`` 是公开占位常量），文件本体恢复受扫描覆盖。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_internal_ip_leak",
    REPO_ROOT / "tools" / "dev" / "check-internal-ip-leak.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_internal_ip_leak"] = _mod
_spec.loader.exec_module(_mod)


def test_device_serial_not_whole_file_allowlisted():
    rel = "backend/core/device_serial.py"
    assert not _mod._is_allowlisted(rel), (
        "device_serial.py 不得整文件放行——否则该文件永远不被扫描（#1655）"
    )


def test_device_serial_content_scans_clean():
    text = (REPO_ROOT / "backend/core/device_serial.py").read_text(encoding="utf-8")
    hits = _mod.scan_text(text, "backend/core/device_serial.py")
    assert hits == [], f"占位常量应经 SAFE_TOKENS 放行，实际命中: {hits}"


def test_placeholder_token_is_safe_token():
    assert "0123456789ABCDEF" in _mod.SAFE_TOKENS


# ── #2432：默认扫描集必须含未跟踪文件（提交前门禁的盲区）────────────────────

def _git(root: Path, *args: str) -> None:
    import subprocess

    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} 失败：{proc.stderr}"


def _repo_with_three_files(tmp_path: Path) -> Path:
    """造一个 git 仓库：已跟踪 1 个、未跟踪 1 个、被 .gitignore 忽略 1 个。"""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (root / "tracked.md").write_text("正文\n", encoding="utf-8")
    (root / "untracked.md").write_text("正文\n", encoding="utf-8")
    (root / "ignored.md").write_text("172.21.15.66\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "tracked.md")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    return root


def test_default_targets_include_untracked_files(tmp_path):
    """新文件在被 `git add` 之前也必须受门禁覆盖——那正是最该拦的时点（#2432）。"""
    root = _repo_with_three_files(tmp_path)
    targets = _mod.default_targets(root)
    assert "tracked.md" in targets
    assert "untracked.md" in targets, (
        "未跟踪文件缺席默认集 = 提交前门禁对新文件完全失明（#2402 现场：本地连绿、CI 才红）"
    )


def test_default_targets_still_respect_gitignore(tmp_path):
    """但不能因此去读 .gitignore 命中的东西（.venv / node_modules / .wt 并行 worktree）。"""
    root = _repo_with_three_files(tmp_path)
    targets = _mod.default_targets(root)
    assert "ignored.md" not in targets
    assert "untracked.md" in targets


def test_untracked_hit_is_reported_end_to_end(tmp_path, monkeypatch, capsys):
    """整链自证：未跟踪文件里的真实内网地址必须被 `main()` 报出来并退出非 0。"""
    root = _repo_with_three_files(tmp_path)
    (root / "untracked.md").write_text("生产控制面 172.21.15.66 不可用。\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "ROOT", root)

    code = _mod.main(["--check"])
    captured = capsys.readouterr()
    reported = captured.out + captured.err   # 明细走 stdout、处置提示走 stderr

    assert code == 1, f"未跟踪文件的命中必须让门禁红，实际退出码 {code}\n{reported}"
    assert "untracked.md" in reported and "172.21.15.66" in reported


def test_ignored_hit_is_not_reported_end_to_end(tmp_path, monkeypatch, capsys):
    """反向边界：同一内容放在被忽略的文件里不得报（否则本地会被 .venv 之类刷爆）。"""
    root = _repo_with_three_files(tmp_path)
    (root / "ignored.md").write_text("生产控制面 172.21.15.66 不可用。\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "ROOT", root)

    code = _mod.main(["--check"])

    assert code == 0, f"被忽略文件不应进扫描集：\n{capsys.readouterr()}"
