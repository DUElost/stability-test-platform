"""#2973：script_guard / pg_error_guard 入口 sys.path 自举回归。

systemd ExecStart 以 `python tools/dev/<probe>.py` 直接跑时 sys.path[0]=tools/dev，
仓库根不在 path。顶层 `from tools.dev…` 若写在 bootstrap 之前 → ModuleNotFoundError，
timer 日日 failed、指标停摆。同形正确样本：`skill_usage_probe.py`（先 insert 再 import）。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBES = (
    REPO_ROOT / "tools" / "dev" / "script_guard_probe.py",
    REPO_ROOT / "tools" / "dev" / "pg_error_guard.py",
)


def test_guard_probes_bootstrap_before_tools_import():
    """源码顺序：sys.path.insert 必须出现在任何 `from tools.dev` / `import tools` 之前。"""
    for script in PROBES:
        text = script.read_text(encoding="utf-8")
        insert_at = text.find("sys.path.insert")
        assert insert_at >= 0, f"{script.name}: 缺少 sys.path.insert 自举"
        # 只看模块顶层 import（忽略注释里的示例字符串意外命中时仍要求 insert 更靠前）
        first_tools = min(
            i
            for i in (
                text.find("\nfrom tools."),
                text.find("\nimport tools"),
                text.find("\nfrom tools "),
            )
            if i >= 0
        )
        assert insert_at < first_tools, (
            f"{script.name}: tools import 先于 sys.path 自举（#2973 复发）"
        )
        assert "Path(__file__).resolve().parents[2]" in text


def test_guard_probes_help_without_pythonpath(tmp_path):
    """仓库外 cwd + 清空 PYTHONPATH：--help 必须能起来（修复前必红）。"""
    env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONPATH"}
    for script in PROBES:
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"{script.name} --help failed (rc={result.returncode}):\n{result.stderr}"
        )
        assert "usage:" in (result.stdout + result.stderr).lower()
