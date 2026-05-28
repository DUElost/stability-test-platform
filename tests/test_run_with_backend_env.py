import importlib.util
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "tools" / "run_with_backend_env.py"


spec = importlib.util.spec_from_file_location("run_with_backend_env", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)

build_subprocess_env = module.build_subprocess_env


def test_build_subprocess_env_prefers_env_file_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "REDIS_URL=redis://127.0.0.1:6379/0\nDATABASE_URL=postgresql://db\n",
        encoding="utf-8",
    )

    merged = build_subprocess_env(
        env_file,
        base_env={
            "REDIS_URL": "redis://localhost:6379/0",
            "KEEP_ME": "1",
        },
    )

    assert merged["REDIS_URL"] == "redis://127.0.0.1:6379/0"
    assert merged["DATABASE_URL"] == "postgresql://db"
    assert merged["KEEP_ME"] == "1"


def test_run_with_backend_env_cli_overrides_parent_process_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("REDIS_URL=redis://127.0.0.1:6379/0\n", encoding="utf-8")
    probe = tmp_path / "print_env.py"
    probe.write_text(
        "import os; print(os.environ.get('REDIS_URL', 'missing'))",
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["REDIS_URL"] = "redis://localhost:6379/0"

    result = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "--env-file",
            str(env_file),
            "--",
            sys.executable,
            str(probe),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "redis://127.0.0.1:6379/0"
