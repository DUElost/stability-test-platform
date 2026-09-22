"""#3101：「通过」的 PlanRun 终态必须只有一个定义，且消费面同口径。

ADR-0048 v1.1 恢复 PARTIAL_SUCCESS 三态后，run 级「成功率」消费面（结果页风险趋势 /
脚本用量）只认 SUCCESS 而分母含 PARTIAL_SUCCESS ⇒ 「设备有失败的 run」同时进分母、
不进分子，成功率无声下降；而链触发与种子验收早已把 PARTIAL_SUCCESS 当通过，三处
判据互相矛盾。

本文件把「唯一来源」与「消费面不再写字面量」都钉住——它们是同一语义的三个消费者，
任何一处单独改动都必须先改常量，从而变成一次有意识的决定而不是静默漂移。

实现说明：本文件**只做源码级断言**，不 import `backend.*`：`backend.models` 包的
`__init__` 会连带 `backend.core.database` 的 DSN 解析，而本仓的 `db_url_guard` 明确
禁止测试指向回环地址（本机 5432 就是生产库）——契约守卫不该依赖运行环境。
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parents[1]

PASSING_STATUS_VALUES = {"SUCCESS", "PARTIAL_SUCCESS"}


def _source(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _literal_set(text: str, name: str) -> set[str]:
    """从源码里取 `name = {"A", "B"}` 的字符串字面量集合（容忍顺序/空白）。"""
    match = re.search(rf"{re.escape(name)}\s*=\s*\{{([^}}]*)\}}", text)
    assert match, f"找不到 {name} 的定义"
    return set(re.findall(r'"([A-Za-z_]+)"', match.group(1)))


def test_shared_constant_is_the_two_passing_states() -> None:
    text = _source("backend/models/enums.py")
    match = re.search(
        r"PASSING_PLAN_RUN_STATUSES[^=]*=\s*frozenset\(\{(.*?)\}\)", text, re.S
    )
    assert match, "backend/models/enums.py 里找不到 PASSING_PLAN_RUN_STATUSES"
    body = match.group(1)
    assert "PlanRunStatus.SUCCESS.value" in body, "共享常量必须含 SUCCESS"
    assert "PlanRunStatus.PARTIAL_SUCCESS.value" in body, "共享常量必须含 PARTIAL_SUCCESS"
    assert "FAILED" not in body, "FAILED 不是通过态"
    assert "RUNNING" not in body, "RUNNING 不是终态"


def test_platform_definitions_of_passing_agree() -> None:
    """三处「通过」定义必须同值（链触发 / 种子验收 / 共享常量）。"""
    chain = _literal_set(
        _source("backend/services/plan_chain_trigger.py"), "TRIGGERABLE_TERMINAL_STATUSES"
    )
    seed = _literal_set(_source("backend/scripts/seed_and_smoke.py"), "PASSING_STATUSES")
    assert chain == PASSING_STATUS_VALUES, (
        f"链触发的可继续终态 {sorted(chain)} 与「通过」定义不一致"
    )
    assert seed == PASSING_STATUS_VALUES, (
        f"种子验收的通过集 {sorted(seed)} 与共享常量不一致"
    )


def test_result_trend_counts_partial_as_success() -> None:
    """结果页风险趋势：分子必须引用共享常量（#3101）。"""
    guard = SourceGuard.of_repo_path("backend/api/routes/results.py")
    # 锚点：确认这段逻辑仍在本文件（否则下面的断言是空守）。
    (guard.anchored('@router.get("/risk-trend"')
          .anchored("success_rate")
          .assert_present("PASSING_PLAN_RUN_STATUSES",
                          why="结果页趋势未引用共享「通过」常量"))
    guard.assert_absent(
        'run.status == "SUCCESS"',
        why="分子仍按单态统计而分母含 PARTIAL_SUCCESS ⇒ 成功率无声下降（#3101）",
    )


def test_script_usage_counts_partial_as_success() -> None:
    """脚本用量：SQL 分子必须与分母同口径（#3101）。"""
    guard = SourceGuard.of_repo_path("backend/api/routes/scripts.py")
    (guard.anchored("step->>'script_version'")
          .anchored("success_count")
          .assert_present("PASSING_PLAN_RUN_STATUSES",
                          why="脚本用量未引用共享「通过」常量"))
    guard.assert_absent(
        "WHERE pr.status = 'SUCCESS'",
        why="SQL 分子仍按单态统计而分母含 PARTIAL_SUCCESS（#3101）",
    )
