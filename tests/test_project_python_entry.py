"""Real shell/argv/cwd probes for #3516 G3; no DB, model or pytest subprocess."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'scripts/project_python.sh'


def git(root, *args):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-C', str(root), *args],
                          check=True, capture_output=True, text=True)


@pytest.fixture
def checkouts(tmp_path):
    main = tmp_path / "repo ' with spaces"
    main.mkdir()
    git(main, 'init', '-q', '--template=')
    (main / 'scripts').mkdir()
    shutil.copy2(SOURCE, main / 'scripts/project_python.sh')
    git(main, 'add', 'scripts/project_python.sh')
    git(main, '-c', 'user.name=Probe', '-c', 'user.email=probe@example.invalid',
        'commit', '-qm', 'fixture entry')
    worktree = tmp_path / "worktree ' with spaces"
    git(main, 'worktree', 'add', '-qb', 'fixture', str(worktree))
    for root in (main, worktree):
        (root / '.venv/bin').mkdir(parents=True)
        (root / '.venv/bin/python').symlink_to(sys.executable)
        (root / 'backend/agent/aee').mkdir(parents=True)
    return main, worktree


@pytest.mark.parametrize('checkout', [0, 1])
@pytest.mark.parametrize('depth', ['', 'backend/agent', 'backend/agent/aee'])
def test_real_entry_uses_own_checkout_in_all_cwds(checkouts, checkout, depth):
    root = checkouts[checkout]
    code = 'import os,sys,json; print(json.dumps([os.getcwd(),sys.executable,sys.argv[1:],sys.stdin.read()]))'
    args = [str(root / 'scripts/project_python.sh'), '-B', '-c', code,
            'a b', "'quotes'", '$(touch SHOULD_NOT_EXIST)', '--flag=value']
    env = os.environ.copy()
    env['VIRTUAL_ENV'] = '/missing/ambient/interpreter'
    env['CDPATH'] = str(root.parent)
    proc = subprocess.run(args, cwd=root / depth, env=env, input='stdin stays literal\n',
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    cwd, executable, argv, stdin = json.loads(proc.stdout)
    assert cwd == str(root)
    assert executable == str(root / '.venv/bin/python')
    assert argv == args[4:] and stdin == 'stdin stays literal\n'
    assert not (root / 'SHOULD_NOT_EXIST').exists()


@pytest.mark.parametrize('checkout', [0, 1])
def test_relative_tool_paths_are_rooted(checkouts, checkout):
    root = checkouts[checkout]
    tool = root / 'scripts/observe.py'
    tool.write_text('from pathlib import Path\nprint(Path.cwd().name)\n')
    proc = subprocess.run([str(root / 'scripts/project_python.sh'), '-B', 'scripts/observe.py'],
                          cwd=root / 'backend/agent/aee', capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == root.name


def test_child_exit_code_preserved(checkouts):
    root = checkouts[0]
    proc = subprocess.run([str(root / 'scripts/project_python.sh'), '-c', 'raise SystemExit(37)'],
                          capture_output=True, text=True)
    assert proc.returncode == 37


@pytest.mark.parametrize('kind', ['missing', 'broken', 'nonexecutable'])
def test_missing_environment_never_falls_back(checkouts, kind):
    main, root = checkouts
    interpreter = root / '.venv/bin/python'
    interpreter.unlink()
    if kind == 'broken':
        interpreter.symlink_to('/missing/project/python')
    elif kind == 'nonexecutable':
        interpreter.write_text('not executable')
    env = os.environ.copy()
    env['VIRTUAL_ENV'] = str(main / '.venv')
    env['PATH'] = str(main / '.venv/bin') + ':' + env['PATH']
    proc = subprocess.run([str(root / 'scripts/project_python.sh'), '-c', 'print("wrong fallback")'],
                          cwd=root / 'backend/agent/aee', env=env, capture_output=True, text=True)
    assert proc.returncode == 1 and '[UNVERIFIED]' in proc.stderr
    assert not proc.stdout


def test_entry_does_not_load_env_files(checkouts):
    root = checkouts[0]
    sentinel = root / 'SHOULD_NOT_EXIST'
    for filename in ('.env', '.env.backend', '.env.test'):
        (root / filename).write_text(f'touch "{sentinel}"\n')
    proc = subprocess.run([str(root / 'scripts/project_python.sh'), '-B', '-c', 'print("ok")'],
                          capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout.strip() == 'ok'
    assert not sentinel.exists()
