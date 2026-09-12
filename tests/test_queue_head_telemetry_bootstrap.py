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
