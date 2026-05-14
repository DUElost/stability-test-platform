import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "prepare_env.py"


def test_prepare_env_creates_missing_target_from_template(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env.runtime"
    template.write_text("JWT_SECRET_KEY=change-me\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--template",
            str(template),
            "--target",
            str(target),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "JWT_SECRET_KEY=change-me\n"
    assert "Created env file from template" in result.stdout


def test_prepare_env_keeps_existing_target_content(tmp_path):
    template = tmp_path / ".env.example"
    target = tmp_path / ".env.runtime"
    template.write_text("JWT_SECRET_KEY=template-value\n", encoding="utf-8")
    target.write_text("JWT_SECRET_KEY=existing-value\n", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--template",
            str(template),
            "--target",
            str(target),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert target.read_text(encoding="utf-8") == "JWT_SECRET_KEY=existing-value\n"
    assert "already exists" in result.stdout


def test_backend_env_templates_are_present_and_unignored():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    result = subprocess.run(
        ["git", "check-ignore", "deploy/control-plane/env/.env.backend.example"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert (REPO_ROOT / ".env.server.example").exists()
    assert (REPO_ROOT / "deploy/control-plane/env/.env.backend.example").exists()
    assert "!.env.server.example" in gitignore
    assert "!deploy/control-plane/env/" in gitignore
    assert "!deploy/control-plane/env/.env.backend.example" in gitignore
    assert result.returncode == 1, result.stdout
