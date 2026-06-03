from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GITIGNORE = REPO_ROOT / ".gitignore"


def test_gitignore_excludes_local_patch_artifacts_and_reference_monolith():
    text = GITIGNORE.read_text(encoding="utf-8")

    assert "patches/" in text
    assert "MonkeyAEEinfo_260523.py" in text
    assert "!.codex/hooks.json" in text
