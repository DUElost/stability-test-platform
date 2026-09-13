"""#1659：queue_head_telemetry 非包入口的 sys.path bootstrap 回归。

以 `python tools/dev/queue_head_telemetry.py` 直接运行时 sys.path[0]=tools/dev，
仓库根不在 path——脚本内 lazy import `tools.dev.ai_work` 会 ModuleNotFoundError
（`--help` 不触发所以漏网）。本测试在**仓库外 cwd** 用 importlib 加载脚本模块，
再 import 该 lazy 依赖，锁定 bootstrap 生效（修复前必红）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "dev" / "queue_head_telemetry.py"


def test_script_bootstrap_enables_tools_import_from_any_cwd(tmp_path):
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('qht', r'{SCRIPT}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules['qht'] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "import tools.dev.ai_work\n"  # collect() 的 lazy 依赖
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,  # 仓库外：只有脚本自身 bootstrap 能救
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_script_bootstrap_is_cwd_independent():
    """bootstrap 用 __file__ 推导仓库根（从任意 cwd 运行都可 import tools）。"""
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[2]" in text
    assert "sys.path.insert" in text


# ── #1792：NEUTRAL 必须计为「满足」，否则产出错误的人工动作指引 ──────────────

REQUIRED = [
    "lint", "CodeQL", "pr-typecheck", "pr-compileall",
    "pr-agent-tests", "pr-migrate-empty-db",
]


def _load_telemetry():
    import importlib.util

    spec = importlib.util.spec_from_file_location("qht_under_test", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["qht_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def _ck(name: str, status: str, conclusion: str) -> dict:
    return {"name": name, "status": status, "conclusion": conclusion}


def test_neutral_conclusion_is_not_blocking():
    """#1792：CodeQL 的 COMPLETED/NEUTRAL 不得计入 blocking。

    实证：#1772 三子分析全 SUCCESS、父 check COMPLETED/NEUTRAL，在 strict=true
    分支保护下**被 GitHub 允许合入**——即 NEUTRAL 是满足态。若本工具判它未满足，
    会产出 REQUIRED_CHECK_FAILED + actionable:true 的错误人工指引。
    """
    mod = _load_telemetry()
    checks = [_ck(n, "COMPLETED", "SUCCESS") for n in REQUIRED]
    checks[1] = _ck("CodeQL", "COMPLETED", "NEUTRAL")
    assert mod.blocking_checks(checks, REQUIRED) == []


def test_transient_neutral_does_not_block():
    """子分析在跑时的瞬态 NEUTRAL（#1775 形态）同样不得计入 blocking。"""
    mod = _load_telemetry()
    checks = [_ck(n, "COMPLETED", "SUCCESS") for n in REQUIRED]
    checks[1] = _ck("CodeQL", "COMPLETED", "NEUTRAL")
    checks[4] = _ck("pr-agent-tests", "IN_PROGRESS", "")
    names = [b["name"] for b in mod.blocking_checks(checks, REQUIRED)]
    assert names == ["pr-agent-tests"], names


def test_failure_conclusion_is_still_blocking():
    """NEUTRAL 的放行不得把真实 FAILURE 一起放过。"""
    mod = _load_telemetry()
    checks = [_ck(n, "COMPLETED", "SUCCESS") for n in REQUIRED]
    checks[1] = _ck("CodeQL", "COMPLETED", "FAILURE")
    names = [b["name"] for b in mod.blocking_checks(checks, REQUIRED)]
    assert names == ["CodeQL"], names
