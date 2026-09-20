# -*- coding: utf-8 -*-
"""#2905：recycler 的异常收敛路径——**写审计，或显式声明豁免**。

背景：三条同族路径里**最高频**的那条不写审计——`_mark_pending_timeout`（PENDING→FAILED）
与 `_mark_patrol_stall`（patrol 断节奏）都有 `record_audit`，而 `_mark_running_timeout`
（RUNNING→UNKNOWN：Agent 掉线 / 租约宽限 / abort 未 ACK）没有。后果：一个 job 变 UNKNOWN
在审计面完全无痕，只剩 `status_reason` 与瞬时指标 `task_run_state_changes`——而本平台把审计
当持久证据链（ADR-0044 D3、ADR-0049 分层保留），且指标重启即失忆、答不了「具体哪些 job」。

本守卫把「新增第四条同族路径、悄悄不写审计」变成**未知即红**：`recycler.py` 里每个
`_mark_*` 函数要么调用 `record_audit*`，要么出现在 `_AUDIT_EXEMPT` 且**写明理由**；
豁免表陈旧（函数已写审计 / 已改名）同样红——不允许豁免沉淀成永久豁免。

局限（有意为之）：纯 AST、只认函数体内**直接**的 `record_audit*` 调用；经其它 helper 间接
收敛的路径不在射程内（那要调用图，成本与误报都不划算）。
"""
from __future__ import annotations

import ast
from pathlib import Path

_RECYCLER = Path(__file__).resolve().parents[1] / "scheduler" / "recycler.py"
_RECORD_FUNCS = {"record_audit", "record_audit_async"}

#: 豁免登记：函数名 → 理由（**必须非空**；空理由 = 没声明，直接红）。
_AUDIT_EXEMPT: dict[str, str] = {
    "_mark_running_timeout": (
        "待裁决（#2905）：RUNNING→UNKNOWN 是否需持久证据尚未裁决——本表只把「不写」钉成"
        "**显式欠账**。裁决为豁免 ⇒ 理由写进函数注释（与另两条的 ADR 依据同形）；"
        "裁决为应写 ⇒ 补 record_audit（action 建议 job_running_timeout，并确认落进 "
        "ADR-0049 的 business 桶）"
    ),
}


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


def audit_consistency_violations(source: str) -> list[str]:
    """每个 `_mark_*` 必须「写审计」或「显式豁免（理由非空）」——否则报违规。"""
    out: list[str] = []
    for name, func in _mark_functions(ast.parse(source)).items():
        if _calls_record_audit(func):
            continue
        if _AUDIT_EXEMPT.get(name, "").strip():
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
    exempted = "_mark_running_timeout"  # 在 _AUDIT_EXEMPT 里且理由非空
    assert audit_consistency_violations(f"def {exempted}(db):\n    pass\n") == []


def test_detector_ignores_non_mark_functions():
    """非 `_mark_*` 的收敛路径不受本判据约束（否则会把无关函数判红）。"""
    assert audit_consistency_violations("def finalize(db):\n    db.commit()\n") == []
