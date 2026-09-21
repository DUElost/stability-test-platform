"""#2981：模板里 check_device 步骤墙钟必须容纳脚本默认 total_budget。

v1.0.2 默认 `total_budget_seconds=150`；引擎墙钟到点 killpg，30s 下重试与
boot 门永远跑不到，失败报文还比 v1.0.1 更差（丢 attempts/history）。
不变式：timeout_seconds >= 脚本默认预算（守卫取 180，含 docstring 建议余量）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "backend/schemas/pipeline_templates"
CHECK_DEVICE_ENTRY = (
    REPO_ROOT / "backend/agent/scripts/check_device/v1.0.2/check_device.py"
)

# 与脚本 docstring「≥180s」及本单验收一致（默认预算 150 + 余量）。
MIN_CHECK_DEVICE_TIMEOUT_S = 180


def _default_total_budget() -> float:
    text = CHECK_DEVICE_ENTRY.read_text(encoding="utf-8")
    m = re.search(
        r'total_budget_seconds["\']?\s*(?:=|:)\s*float\([^)]*default\s*=\s*(\d+)',
        text,
    )
    if m:
        return float(m.group(1))
    m = re.search(
        r'args\.get\(\s*["\']total_budget_seconds["\']\s*,\s*(\d+)\s*\)',
        text,
    )
    assert m, "check_device v1.0.2 找不到 total_budget_seconds 默认值"
    return float(m.group(1))


def _iter_check_device_timeouts(obj: object):
    if isinstance(obj, dict):
        if obj.get("action") == "script:check_device":
            yield obj.get("timeout_seconds"), obj.get("version")
        for v in obj.values():
            yield from _iter_check_device_timeouts(v)
    elif isinstance(o := obj, list):
        for item in o:
            yield from _iter_check_device_timeouts(item)


def test_check_device_template_timeout_covers_budget() -> None:
    budget = _default_total_budget()
    assert budget <= MIN_CHECK_DEVICE_TIMEOUT_S, (
        f"守卫下限 {MIN_CHECK_DEVICE_TIMEOUT_S}s 应 ≥ 脚本默认预算 {budget}s"
    )
    bad: list[str] = []
    seen = 0
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for timeout, version in _iter_check_device_timeouts(data):
            seen += 1
            if not isinstance(timeout, (int, float)) or timeout < MIN_CHECK_DEVICE_TIMEOUT_S:
                bad.append(
                    f"{path.name}: check_device@{version} timeout={timeout!r} "
                    f"< {MIN_CHECK_DEVICE_TIMEOUT_S}s (budget default={budget:g}s)"
                )
    assert seen >= 8, f"预期 ≥8 处 check_device 引用，实际 {seen}"
    assert not bad, (
        "模板 check_device 墙钟容纳不下脚本重试预算（#2981）:\n  " + "\n  ".join(bad)
    )


def test_guard_floor_is_above_script_budget() -> None:
    """下限必须严格高于默认预算，否则 150s 墙钟在临界路径仍可能被杀。"""
    assert MIN_CHECK_DEVICE_TIMEOUT_S >= _default_total_budget() + 30
