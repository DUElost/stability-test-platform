"""#2981 / #3087：模板里每个脚本步骤的墙钟必须容纳**该版本脚本声明的预算**。

`#2981` 修了 check_device（`timeout_seconds` 30→180）并留下守卫
（原名 `tests/test_check_device_template_timeout_2981.py`，即本文件的前身）。但那份守卫把
action **硬编码**在判据里（`_iter_check_device_timeouts`），于是同一窗口内 `ensure_root`
以**完全相同**的形态复发：版本已提到 `1.0.2`（其 docstring 自陈「步骤超时必须容纳
`total_budget_seconds`（默认 150s）」），墙钟却仍停在 60s —— 引擎到点 `killpg`，boot 门与
第二次探测跑不到，v1.0.2 新增的 `attempts=`/`history=` 证据一行都输出不来。#2981 自己的
复核评论写明过这条不变式：「**抬超时必须与版本号同事务**」；它第二次被违反时，守卫一声不吭。

所以判据改成**全量不变式**，不维护可漏的名单（#3087 的建议方向）：

1. 遍历全部模板，取每个 `action: script:<name>` 步骤的 `(name, version, timeout_seconds)`；
2. 定位 `backend/agent/scripts/<name>/v<version>/<name>.py`，**解析**它声明的墙钟预算
   （当前两族有此声明：`check_device` / `ensure_root` 的 `total_budget_seconds`，默认 150）；
3. 声明了预算的族 ⇒ 断言 `timeout_seconds >= 预算 + _MARGIN`；
4. 没声明预算的族 ⇒ 无可比对象，跳过；但**被覆盖的族数有下限**（反空转）：
   正则失效导致「一个族都覆盖不到」时本文件必须红，而不是静默变成恒真。

为什么用「解析脚本源码」而不是「在判据里抄一份预算表」：抄一份就是同一事实的第二份权威
（本仓反复强调的债），而脚本侧声明是**可解析的结构化事实**。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_DIR = REPO_ROOT / "backend" / "schemas" / "pipeline_templates"
SCRIPTS_DIR = REPO_ROOT / "backend" / "agent" / "scripts"

#: 余量：与 #2981 落地时的口径一致（默认预算 150 → 墙钟 ≥180）。预算不是墙钟，步骤还要
#: 留出收尾/回 HOME/写 stdout 的时间，所以判据是「预算 + 余量」而不是「≥ 预算」。
_MARGIN = 30

#: 预算声明的解析式。脚本侧形态暂不统一，按顺序尝试；**一个都不命中 = 该族不声明预算**
#: （跳过、不计入覆盖数）。覆盖数有下限（见 `_MIN_COVERED_FAMILIES`），故正则失效会红。
_BUDGET_PATTERNS = (
    r'args\.get\(\s*["\']total_budget_seconds["\']\s*,\s*(\d+)\s*\)',
    r'total_budget_seconds[^\n=]*=\s*float\(\s*args\.get\(\s*["\']total_budget_seconds["\']\s*,\s*(\d+)\s*\)',
)

#: 反空转下限：2026-09-22 实测恰有 2 族声明墙钟预算（check_device / ensure_root，均 150）。
#: 新增第三个声明预算的族时把它抬上去；降到 0 说明解析口径失效，必须红。
_MIN_COVERED_FAMILIES = 2


def _declared_budget(script: Path) -> int | None:
    """该脚本声明的墙钟预算（秒）；没有声明返回 None。"""
    text = script.read_text(encoding="utf-8")
    for pattern in _BUDGET_PATTERNS:
        if match := re.search(pattern, text):
            return int(match.group(1))
    return None


def _iter_script_steps():
    """产出 (模板名, action, 脚本名, 版本, timeout_seconds)。"""
    for path in sorted(TEMPLATES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))

        def walk(obj):
            if isinstance(obj, dict):
                action = obj.get("action")
                if isinstance(action, str) and action.startswith("script:"):
                    yield action, action[len("script:") :], obj.get("version"), obj.get(
                        "timeout_seconds"
                    )
                for value in obj.values():
                    yield from walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    yield from walk(item)

        for action, name, version, timeout in walk(data):
            yield path.name, action, name, version, timeout


def _covered_families() -> dict[tuple[str, str], int]:
    """{(脚本名, 版本): 预算} —— 模板里出现、且脚本声明了预算的族。"""
    covered: dict[tuple[str, str], int] = {}
    for _tpl, _action, name, version, _timeout in _iter_script_steps():
        script = SCRIPTS_DIR / str(name) / f"v{version}" / f"{name}.py"
        if not script.exists():
            continue
        if (budget := _declared_budget(script)) is not None:
            covered[(str(name), str(version))] = budget
    return covered


def _covers(timeout: object, budget: int) -> bool:
    return isinstance(timeout, int) and timeout >= budget + _MARGIN


def test_template_step_timeout_covers_declared_script_budget() -> None:
    offenders: list[str] = []
    for tpl, action, name, version, timeout in _iter_script_steps():
        script = SCRIPTS_DIR / str(name) / f"v{version}" / f"{name}.py"
        assert script.exists(), f"{tpl}: {action} v{version} 的脚本文件不存在（{script}）"
        budget = _declared_budget(script)
        if budget is None:
            continue
        if not _covers(timeout, budget):
            offenders.append(
                f"{tpl}: {action} v{version} timeout_seconds={timeout!r} < "
                f"预算 {budget} + 余量 {_MARGIN} —— 引擎到点 killpg，脚本声明的重试/证据"
                "全都跑不到（#2981/#3087：抬超时必须与版本号同事务）"
            )
    assert not offenders, "模板墙钟装不下脚本声明的预算：\n  " + "\n  ".join(offenders)


def test_budget_scanner_is_not_vacuous() -> None:
    """反空转：解析口径失效时不得静默退化成「零族被覆盖」。"""
    covered = _covered_families()
    assert len(covered) >= _MIN_COVERED_FAMILIES, (
        f"只解析出 {len(covered)} 个声明预算的族（应 ≥{_MIN_COVERED_FAMILIES}）："
        "脚本侧声明形态变了或正则失效——先修本文件的解析面，不接受空集"
    )
    assert all(budget > 0 for budget in covered.values()), f"预算取值异常：{covered}"


def test_budget_parser_and_predicate_have_teeth() -> None:
    """红绿双向：对真实脚本取值（解析器），并对 #3087 的形状判红（谓词）。"""
    check_device = SCRIPTS_DIR / "check_device" / "v1.0.2" / "check_device.py"
    ensure_root = SCRIPTS_DIR / "ensure_root" / "v1.0.2" / "ensure_root.py"
    assert _declared_budget(check_device) == 150, "check_device 的预算解析失真"
    assert _declared_budget(ensure_root) == 150, "ensure_root 的预算解析失真"

    assert not _covers(60, 150), "#3087 的形状（60s 配 150s 预算）被放行了"
    assert not _covers(150, 150), "「恰好等于预算」被放行了（无收尾余量）"
    assert not _covers(None, 150), "缺 timeout_seconds 被放行了"
    assert not _covers("180", 150), "字符串形态的 timeout 被放行了"
    assert _covers(180, 150), "现行口径（180 配 150）被误杀"
