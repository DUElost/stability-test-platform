"""#3109：前端 EMPTY_LIFECYCLE 是第二个生产者，必须落入同一守卫作用面。

模板守卫只 glob `backend/schemas/pipeline_templates/*.json`；但
`usePlanEditForm` 原样提交 `planEditUtils.ts` 的 `EMPTY_LIFECYCLE`。
它曾钉 check_device 1.0.0 / 30s / retry 0——正是 #2981 判为致命的那组值。
#3109 对齐 8 个种子模板同值，并让本守卫解析该 TS 文件参与断言。

原落点 `tests/test_check_device_template_timeout_2981.py` 已由 #3087 泛化为
`tests/test_template_step_timeout_vs_script_budget.py`（模板侧全量不变式）；
本文件承接 #3109 专属的「第二生产者」覆盖，避免跟模板预算守卫纠缠。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "backend" / "schemas" / "pipeline_templates"
PLAN_EDITOR_SOURCE = REPO_ROOT / "frontend" / "src" / "pages" / "orchestration" / "planEditUtils.ts"

# 与脚本 docstring「≥180s」及 #2981/#3087 验收一致（默认预算 150 + 余量 30）。
MIN_CHECK_DEVICE_TIMEOUT_S = 180


def _iter_check_device_timeouts(obj: object):
    if isinstance(obj, dict):
        action = obj.get("action")
        if action == "script:check_device":
            yield obj.get("timeout_seconds"), obj.get("version")
        for value in obj.values():
            yield from _iter_check_device_timeouts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_check_device_timeouts(item)


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
