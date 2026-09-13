from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = REPO_ROOT / ".gitignore"


def test_gitignore_excludes_local_patch_artifacts_and_reference_monolith():
    text = GITIGNORE.read_text(encoding="utf-8")

    assert "patches/" in text
    assert "MonkeyAEEinfo_260523.py" in text
    assert "!.codex/hooks.json" in text
    assert "**/.claude/plan/" in text


def test_gitignore_excludes_cursor_cli_temp_outputs():
    """Cursor CLI 在主 checkout 根产生的 .cursor_* 临时输出不得入库。"""
    text = GITIGNORE.read_text(encoding="utf-8")

    assert ".cursor_*" in text, "缺少 Cursor CLI 临时输出的忽略规则"
