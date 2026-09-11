"""approve-pending-workflow-runs.sh 的分支/同仓过滤回归（R15-F01 / #1293）。

用假的 `gh` 放在 PATH 前面，捕获查询与审批调用，验证：
1. 查询用 REST 支持的 `branch=` 参数（不是被忽略的 `head_branch=`）；
2. 仅审批 head_branch 与 head_repository 都匹配的同仓运行；
3. fork / 跨分支运行即使出现在响应里也不被审批。
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "ci" / "approve-pending-workflow-runs.sh"

_REPO = "acme/widgets"
_BRANCH = "fix/123-target-branch"


def _write_fake_gh(bindir: Path, runs_json: dict, approved_log: Path) -> None:
    runs_file = bindir / "runs.json"
    runs_file.write_text(json.dumps(runs_json), encoding="utf-8")
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "args=(\"$@\")\n"
        f"approved_log={str(approved_log)!r}\n"
        f"runs_file={str(runs_file)!r}\n"
        "if [ \"${args[0]:-}\" != 'api' ]; then echo \"unexpected gh: ${args[*]}\" >&2; exit 2; fi\n"
        "path=\"${args[1]:-}\"\n"
        "case \"$path\" in\n"
        "  *'/actions/runs?'*)\n"
        "    cat \"$runs_file\"\n"
        "    ;;\n"
        "  */actions/runs/*/approve)\n"
        "    run_id=\"$(printf '%s' \"$path\" | sed -E 's#.*/actions/runs/([0-9]+)/approve#\\1#')\"\n"
        "    printf '%s\\n' \"$run_id\" >> \"$approved_log\"\n"
        "    ;;\n"
        "  *)\n"
        "    echo \"unexpected gh path: $path\" >&2\n"
        "    exit 2\n"
        "    ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)


def _run_script(bindir: Path, approved_log: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = _REPO
    env["PATH"] = f"{bindir}:{env['PATH']}"
    return subprocess.run(
        ["bash", str(_SCRIPT), _BRANCH],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_approves_only_matching_same_repo_runs(tmp_path):
    approved_log = tmp_path / "approved.log"
    approved_log.write_text("", encoding="utf-8")
    runs = {
        "workflow_runs": [
            {"id": 1, "name": "ok", "head_branch": _BRANCH,
             "head_repository": {"full_name": _REPO}},
            {"id": 2, "name": "wrong-branch", "head_branch": "main",
             "head_repository": {"full_name": _REPO}},
            {"id": 3, "name": "fork", "head_branch": _BRANCH,
             "head_repository": {"full_name": "attacker/fork"}},
        ]
    }
    _write_fake_gh(tmp_path, runs, approved_log)

    result = _run_script(tmp_path, approved_log)

    assert result.returncode == 0, result.stderr
    approved = approved_log.read_text(encoding="utf-8").split()
    assert approved == ["1"], f"only the same-repo matching run may be approved: {approved}"


def test_query_uses_supported_branch_param(tmp_path):
    """假 gh 只在收到 `branch=` 时返回运行；收到 head_branch 即报 unexpected。"""
    approved_log = tmp_path / "approved.log"
    approved_log.write_text("", encoding="utf-8")
    # 这里刻意让 fake gh 对查询参数做断言：脚本若仍用 head_branch=，脚本内
    # 会走 `|| echo '{"workflow_runs":[]}'`，count=0 → 不审批；我们额外用
    # 一个只对 branch= 响应、对 head_branch 打印告警的 fake。
    gh = tmp_path / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "args=(\"$@\")\n"
        "path=\"${args[1]:-}\"\n"
        "case \"$path\" in\n"
        "  *'head_branch='*)\n"
        "    echo 'USED_HEAD_BRANCH' >> \"${GH_PARAM_LOG:?}\"\n"
        "    printf '{\"workflow_runs\":[]}'\n"
        "    ;;\n"
        "  *'branch='*)\n"
        "    echo 'USED_BRANCH' >> \"${GH_PARAM_LOG:?}\"\n"
        f"    printf '%s' '{json.dumps({'workflow_runs': []})}'\n"
        "    ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)

    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = _REPO
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    param_log = tmp_path / "params.log"
    env["GH_PARAM_LOG"] = str(param_log)
    subprocess.run(
        ["bash", str(_SCRIPT), _BRANCH], capture_output=True, text=True,
        env=env, check=False,
    )

    assert param_log.read_text(encoding="utf-8").split() == ["USED_BRANCH"]


@pytest.mark.parametrize("branch", ["dependabot/npm_and_yarn/frontend/frontend-major-x"])
def test_excluded_branch_short_circuits(tmp_path, branch):
    approved_log = tmp_path / "approved.log"
    approved_log.write_text("", encoding="utf-8")
    _write_fake_gh(tmp_path, {"workflow_runs": []}, approved_log)

    env = dict(os.environ)
    env["GITHUB_REPOSITORY"] = _REPO
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    result = subprocess.run(
        ["bash", str(_SCRIPT), branch], capture_output=True, text=True,
        env=env, check=False,
    )
    assert result.returncode == 0
    assert "excluded" in result.stdout.lower()
