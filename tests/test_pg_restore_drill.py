import os
import re
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RESTORE_SCRIPT = REPO_ROOT / "scripts/pg_restore_test.sh"

# stub psql：按调用形态分派；STUB_* 环境变量由测试注入。
# 恢复分支校验 ON_ERROR_STOP=1 确实被传入（缺失即失败），使静态参数成为运行时可观察行为。
STUB_PSQL = """#!/usr/bin/env bash
args="$*"
case "$args" in
  *"CREATE DATABASE"*) exit 0 ;;
  *"DROP DATABASE"*) exit 0 ;;
  *"information_schema.tables"*) echo "${STUB_TABLE_COUNT:-5}"; exit "${STUB_TABLE_RC:-0}" ;;
  *"FROM host"*) echo "${STUB_HOST_COUNT:-1}"; exit "${STUB_HOST_RC:-0}" ;;
  *"FROM plan"*) echo "${STUB_PLAN_COUNT:-1}"; exit "${STUB_PLAN_RC:-0}" ;;
esac
cat >/dev/null
if [[ "$args" != *"ON_ERROR_STOP=1"* ]]; then
  echo "stub psql: restore invoked without ON_ERROR_STOP" >&2
  exit 2
fi
exit "${STUB_RESTORE_RC:-0}"
"""

STUB_GUNZIP = """#!/usr/bin/env bash
exit 0
"""


def _run_restore_drill(tmp_path, **env_overrides):
    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for name, body in (("psql", STUB_PSQL), ("gunzip", STUB_GUNZIP)):
        stub = stubs / name
        stub.write_text(body, encoding="utf-8")
        stub.chmod(0o755)

    backup = tmp_path / "backup.sql.gz"
    backup.write_text("stub", encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    env.update(env_overrides)

    return subprocess.run(
        ["bash", str(RESTORE_SCRIPT), str(backup)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_restore_drill_passes_when_restore_and_checks_ok(tmp_path):
    result = _run_restore_drill(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "Restore drill PASSED" in result.stdout


def test_restore_drill_fails_on_mid_restore_sql_error(tmp_path):
    result = _run_restore_drill(tmp_path, STUB_RESTORE_RC="1")

    assert result.returncode != 0
    assert "PASSED" not in result.stdout


def test_restore_drill_fails_on_too_few_tables(tmp_path):
    result = _run_restore_drill(tmp_path, STUB_TABLE_COUNT="2")

    assert result.returncode != 0
    assert "PASSED" not in result.stdout


def test_restore_drill_fails_when_count_query_errors(tmp_path):
    result = _run_restore_drill(tmp_path, STUB_TABLE_RC="1")

    assert result.returncode != 0
    assert "PASSED" not in result.stdout


def test_restore_requires_on_error_stop():
    text = RESTORE_SCRIPT.read_text(encoding="utf-8")
    restore_line = next(line for line in text.splitlines() if "gunzip" in line and "psql" in line)

    assert "-v ON_ERROR_STOP=1" in restore_line


def test_table_shortfall_is_fatal_not_warning():
    text = RESTORE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r'if \[ "\$\{TABLE_COUNT\}" -lt 5 \]; then(?P<body>.*?)fi',
        text,
        re.DOTALL,
    )

    assert match is not None, "table count guard not found"
    assert "exit 1" in match.group("body")
    assert "WARNING" not in text
