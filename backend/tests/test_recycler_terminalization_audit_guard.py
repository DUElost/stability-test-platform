# -*- coding: utf-8 -*-
"""#2905：recycler 的异常收敛路径——**写审计，或显式声明豁免**。

背景：三条同族路径里**最高频**的那条曾不写审计——`_mark_pending_timeout`（PENDING→FAILED）
与 `_mark_patrol_stall`（patrol 断节奏）都有 `record_audit`，而 `_mark_running_timeout`
（RUNNING→UNKNOWN：Agent 掉线 / 租约宽限 / abort 未 ACK）没有。后果：一个 job 变 UNKNOWN
在审计面完全无痕，只剩 `status_reason` 与瞬时指标 `task_run_state_changes`——而本平台把审计
当持久证据链（ADR-0044 D3、ADR-0049 分层保留），且指标重启即失忆、答不了「具体哪些 job」。
**#2905 已裁决为「写审计」**：`_mark_running_timeout` 补 `record_audit(action=
"job_running_timeout")`，按 ADR-0049 D2 落 business 默认桶——本文件从此是**回归守卫**。

本守卫把「新增第四条同族路径、悄悄不写审计」变成**未知即红**：`recycler.py` 里每个
`_mark_*` 函数要么调用 `record_audit*`，要么出现在 `_AUDIT_EXEMPT` 且**写明理由**；
豁免表陈旧（函数已写审计 / 已改名）同样红——不允许豁免沉淀成永久豁免。当前登记表为空：
三条路径都写了审计，这本身就是判据的一部分。

局限（有意为之）：纯 AST、只认函数体内**直接**的 `record_audit*` 调用；经其它 helper 间接
收敛的路径不在射程内（那要调用图，成本与误报都不划算）。
"""
from __future__ import annotations

import ast
from pathlib import Path

_RECYCLER = Path(__file__).resolve().parents[1] / "scheduler" / "recycler.py"
_RECORD_FUNCS = {"record_audit", "record_audit_async"}

#: 豁免登记：函数名 → 理由（**必须非空**；空理由 = 没声明，直接红）。
#: #2905 裁决落地后三条路径都写了审计 ⇒ 当前为空。留空表本身也是判据的一部分：
#: 新增一条不写审计的 `_mark_*` 会立刻在这里现形（没有人替它声明豁免）。
_AUDIT_EXEMPT: dict[str, str] = {}


def _mark_functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("_mark_")
    }


def _calls_record_audit(func: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Call) and getattr(node.func, "id", None) in _RECORD_FUNCS
        for node in ast.walk(func)
    )


def audit_consistency_violations(
    source: str, exempt: dict[str, str] | None = None
) -> list[str]:
    """每个 `_mark_*` 必须「写审计」或「显式豁免（理由非空）」——否则报违规。

    `exempt` 可注入：判据自测不该依赖仓内登记表**当前是否有条目**（#2905 裁决后它是空的），
    否则「把表清空」这种正当改动会连带把自测打红。默认仍走仓内登记表。
    """
    rules = _AUDIT_EXEMPT if exempt is None else exempt
    out: list[str] = []
    for name, func in _mark_functions(ast.parse(source)).items():
        if _calls_record_audit(func):
            continue
        if rules.get(name, "").strip():
            continue
        out.append(
            f"{name}:{func.lineno} 既不调 record_audit 也不在 _AUDIT_EXEMPT（#2905）"
        )
    return out


# ── 仓库面 ───────────────────────────────────────────────────────────────────


def test_recycler_mark_paths_are_all_accounted_for():
    source = _RECYCLER.read_text(encoding="utf-8")
    assert _mark_functions(ast.parse(source)), (
        "recycler.py 里找不到 `_mark_*` 函数——判据失效（改名了？），勿静默放行"
    )
    violations = audit_consistency_violations(source)
    assert not violations, (
        "异常收敛路径的审计覆盖出现未知缺口（写审计，或登记豁免并写明理由）：\n  "
        + "\n  ".join(violations)
    )


def test_exemptions_are_not_stale_and_have_reasons():
    """豁免不许沉淀：函数已写审计或已改名 ⇒ 条目必须删；理由为空 ⇒ 等于没声明。"""
    functions = _mark_functions(ast.parse(_RECYCLER.read_text(encoding="utf-8")))
    stale = [
        name for name, func in functions.items()
        if name in _AUDIT_EXEMPT and _calls_record_audit(func)
    ] + [name for name in _AUDIT_EXEMPT if name not in functions]
    assert not stale, f"这些豁免已不再需要（已写审计 / 函数已改名）：{stale}——请删除对应条目"
    for name, reason in _AUDIT_EXEMPT.items():
        assert reason.strip(), f"{name} 的豁免理由为空——「悄悄不写」正是本单要防的形态"


# ── 判据自身（红绿双向）──────────────────────────────────────────────────────


def test_detector_flags_an_unaccounted_mark_function():
    bad = "def _mark_brand_new(db):\n    db.commit()\n"
    violations = audit_consistency_violations(bad)
    assert len(violations) == 1 and "_mark_brand_new" in violations[0], violations


def test_detector_accepts_audit_or_documented_exemption():
    with_audit = "def _mark_ok(db):\n    record_audit(db, action='job_terminalized')\n"
    assert audit_consistency_violations(with_audit) == []
    # 合成豁免表（不依赖仓内登记表现状）：有理由 ⇒ 放行，空理由 / 缺理由 ⇒ 仍红。
    body = "def _mark_legacy(db):\n    pass\n"
    assert audit_consistency_violations(body, exempt={"_mark_legacy": "历史路径，依据写在函数注释"}) == []
    assert len(audit_consistency_violations(body, exempt={"_mark_legacy": "   "})) == 1
    assert len(audit_consistency_violations(body, exempt={})) == 1


def test_detector_ignores_non_mark_functions():
    """非 `_mark_*` 的收敛路径不受本判据约束（否则会把无关函数判红）。"""
    assert audit_consistency_violations("def finalize(db):\n    db.commit()\n") == []
