"""Behavior regressions for #3516 G2; real launcher and isolated source trees."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools.dev import check_governance_surface as governance
from tools.dev import codex_stop_check as stop

ROOT = Path(__file__).resolve().parents[1]
HOOKS = json.loads((ROOT / '.codex/hooks.json').read_text())['hooks']


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'worktree with spaces'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', '--template=', str(root)], check=True)
    for folder in ('tools/dev', 'backend/agent/aee', 'frontend/node_modules/typescript/bin', '.venv/bin'):
        (root / folder).mkdir(parents=True)
    shutil.copyfile(ROOT / 'tools/dev/codex_stop_check.py', root / 'tools/dev/codex_stop_check.py')
    (root / '.venv/bin/python').symlink_to(sys.executable)
    (root / 'backend/good.py').write_text('answer = 42\n')
    (root / 'frontend/tsconfig.json').write_text('{}')
    (root / 'frontend/typecheck-exit').write_text('0')
    # The installed-compiler fixture executes with real Node, never npx/npm.
    (root / 'frontend/node_modules/typescript/bin/tsc').write_text(
        "require('fs').writeFileSync('observed-cwd', process.cwd());\n"
        "process.exit(Number(require('fs').readFileSync('typecheck-exit', 'utf8')));\n"
    )
    return root


def invoke(root, kind, depth='backend/agent/aee'):
    hook = next(h for h in HOOKS['Stop'][0]['hooks'] if h['command'].endswith(kind))
    return subprocess.run(['sh', '-c', hook['command']], cwd=root / depth,
                          capture_output=True, text=True, timeout=55)


@pytest.mark.parametrize('depth', ['', 'backend/agent', 'backend/agent/aee'])
@pytest.mark.parametrize('kind', ['typecheck', 'compileall'])
def test_real_command_resolves_root_in_space_named_tree(repo, depth, kind):
    if kind == 'typecheck' and not shutil.which('node'):
        pytest.skip('real Node unavailable')
    proc = invoke(repo, kind, depth)
    assert proc.returncode == 0, proc.stderr
    message = json.loads(proc.stdout)['systemMessage']
    assert f'[PASS] Codex Stop {kind}' in message
    assert not list(repo.rglob('__pycache__'))
    if kind == 'typecheck':
        assert (repo / 'frontend/observed-cwd').read_text() == str(repo / 'frontend')


@pytest.mark.parametrize('kind', ['typecheck', 'compileall'])
def test_existing_red_is_feedback_after_edit_not_patch_veto(repo, kind):
    assert 'PreToolUse' not in HOOKS
    if kind == 'typecheck':
        if not shutil.which('node'):
            pytest.skip('real Node unavailable')
        source = repo / 'frontend/typecheck-exit'
        source.write_text('1')
        repaired = '0'
    else:
        source = repo / 'backend/good.py'
        source.write_text('if broken\n')
        repaired = 'answer = 42\n'
    proc = invoke(repo, kind)
    assert proc.returncode == 1 and '[FAIL]' in proc.stderr
    # No pre-edit quality hook: the correction can be applied while the tree is red.
    source.write_text(repaired)
    proc = invoke(repo, kind)
    assert proc.returncode == 0 and '[PASS]' in json.loads(proc.stdout)['systemMessage']


@pytest.mark.parametrize('missing', ['.venv/bin/python', 'backend', 'frontend/node_modules/typescript/bin/tsc'])
def test_missing_target_never_silently_passes(repo, missing):
    target = repo / missing
    shutil.rmtree(target) if target.is_dir() else target.unlink()
    kind = 'typecheck' if missing.startswith('frontend') else 'compileall'
    proc = invoke(repo, kind)
    assert proc.returncode == 1 and '[UNVERIFIED]' in proc.stderr


def test_no_git_root_never_passes(tmp_path):
    hook = HOOKS['Stop'][0]['hooks'][0]
    proc = subprocess.run(['sh', '-c', hook['command']], cwd=tmp_path,
                          capture_output=True, text=True)
    assert proc.returncode == 1 and '[UNVERIFIED]' in proc.stderr


def test_timeout_never_passes(repo, monkeypatch):
    monkeypatch.setattr(stop, 'ROOT', repo)
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 45)
    monkeypatch.setattr(stop.subprocess, 'run', timeout)
    assert stop.run_check('compileall') == ('UNVERIFIED', 'TimeoutExpired')


@pytest.mark.parametrize('depth', ['', 'backend/agent', 'backend/agent/aee'])
def test_symlink_matches_actual_canonical_contract(tmp_path, depth):
    folder = tmp_path / depth
    folder.mkdir(parents=True, exist_ok=True)
    canonical = folder / 'AGENTS.md'
    canonical.write_text('# canonical\n')
    entry = folder / 'CLAUDE.md'
    for target in ('AGENTS.md', './AGENTS.md', str(canonical)):
        assert governance.check_claude_entry_form('', True, target, str(entry)) == []
    external = tmp_path / 'outside/AGENTS.md'
    external.parent.mkdir(exist_ok=True)
    external.write_text('# impostor\n')
    assert governance.check_claude_entry_form('', True, str(external), str(entry))
    assert governance.check_claude_entry_form('', True, 'missing/AGENTS.md', str(entry))
    canonical.unlink()
    assert governance.check_claude_entry_form('', True, 'AGENTS.md', str(entry))


@pytest.mark.parametrize('chain', ['valid', 'external', 'loop'])
def test_symlink_multihop_is_resolved(tmp_path, chain):
    canonical = tmp_path / 'AGENTS.md'
    canonical.write_text('# canonical\n')
    bridge = tmp_path / 'bridge'
    if chain == 'valid':
        bridge.symlink_to('AGENTS.md')
    elif chain == 'external':
        outside = tmp_path / 'outside/AGENTS.md'
        outside.parent.mkdir()
        outside.write_text('# other\n')
        bridge.symlink_to(outside)
    else:
        bridge.symlink_to('bridge')
    issues = governance.check_claude_entry_form('', True, 'bridge', str(tmp_path / 'CLAUDE.md'))
    assert bool(issues) == (chain != 'valid')


def test_canonical_itself_must_not_resolve_outside(tmp_path):
    outside = tmp_path / 'outside.md'
    outside.write_text('# impostor\n')
    (tmp_path / 'AGENTS.md').symlink_to(outside)
    assert governance.check_claude_entry_form('', True, 'AGENTS.md', str(tmp_path / 'CLAUDE.md'))
