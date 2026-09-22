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


# ---------------------------------------------------------------------------
# #3109：模板之外还有**第二个生产者**——前端计划编辑器的 EMPTY_LIFECYCLE。
# 它被 usePlanEditForm 原样提交，所以只扫 backend/schemas/pipeline_templates/*.json
# 的守卫结构上看不见它（原来的 check_device 1.0.0 / 30s / retry 0 就是这么留下的）。
# ---------------------------------------------------------------------------

PLAN_EDITOR_SOURCE = REPO_ROOT / "frontend/src/pages/orchestration/planEditUtils.ts"


def _plan_editor_check_device_step() -> dict[str, str]:
    """从 planEditUtils.ts 取 EMPTY_LIFECYCLE 的 check_device 步骤字段。

    对象很小、形态固定，故用正则而非引入 TS 解析器（保持守卫零依赖）。
    """
    text = PLAN_EDITOR_SOURCE.read_text(encoding="utf-8")
    block = re.search(r"EMPTY_LIFECYCLE[^{]*\{(.*?)\n\};", text, re.S)
    assert block, "planEditUtils.ts 里找不到 EMPTY_LIFECYCLE 字面量（守卫作用面已失效）"

    def field(name: str) -> str:
        m = re.search(rf"\b{name}\s*:\s*'?([^,'\n]+)'?", block.group(1))
        assert m, f"EMPTY_LIFECYCLE 缺字段 {name}"
        return m.group(1).strip().strip("'").strip('"')

    return {
        "action": field("action"),
        "version": field("version"),
        "timeout_seconds": field("timeout_seconds"),
        "retry": field("retry"),
    }


def _template_check_device_pins() -> set[tuple[str, float]]:
    pins: set[tuple[str, float]] = set()
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for timeout, version in _iter_check_device_timeouts(data):
            pins.add((str(version), float(timeout)))
    return pins


def test_plan_editor_empty_step_is_within_the_guard_surface() -> None:
    """第二个生产者必须满足同一条不变式（它曾被守卫完全漏掉）。"""
    step = _plan_editor_check_device_step()
    assert step["action"] == "script:check_device", step
    assert float(step["timeout_seconds"]) >= MIN_CHECK_DEVICE_TIMEOUT_S, (
        f"前端 EMPTY_LIFECYCLE 的 check_device 墙钟 {step['timeout_seconds']}s "
        f"< {MIN_CHECK_DEVICE_TIMEOUT_S}s：用编辑器新建的 Plan 会在 boot 门前被墙钟杀掉"
    )
    assert float(step["retry"]) >= 1, (
        "retry 0 + 短墙钟 ⇒ v1.0.2 的 boot 门与有界重试永远跑不到（#2981 的形态）"
    )


def test_plan_editor_step_does_not_drift_from_seed_templates() -> None:
    """两个生产者不得漂移：前端 (version, timeout) 必须落在模板 pin 集内。"""
    step = _plan_editor_check_device_step()
    pins = _template_check_device_pins()
    assert pins, "模板里没有 check_device 引用——守卫作用面失效"
    assert (step["version"], float(step["timeout_seconds"])) in pins, (
        f"前端 EMPTY_LIFECYCLE 钉 ({step['version']}, {step['timeout_seconds']}s)，"
        f"而模板钉 {sorted(pins)}；新建 Plan 走哪条路会拿到不同版本"
    )
