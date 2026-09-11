"""Prometheus 告警规则契约（#1257 / R14-F11）。

两层校验：

1. **结构层（恒跑）**：规则表达式的指标名与标签名必须存在于
   ``backend/core/metrics.py`` 的注册表——未知指标、未知标签、直方图裸用
   基础名（如 ``stability_x_observed`` 而非 ``_count/_bucket/_sum``）都拦。
   2026-09-10 审计发现的三条漂移（``status``/``job`` 标签、直方图裸名）由
   这一层机械拦截。
2. **场景层（promtool 可用时跑）**：``promtool test rules`` 用真实标签形状的
   样本证明修复过的规则可触发；runner 无 promtool 时显式 skip，不影响结构层。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

# backend.core.database 在导入期解析 DATABASE_URL（root tests 无 conftest 注入）。
# 先例：tests/test_leader_election.py；CI 已设 dummy URL，setdefault 不覆盖。
os.environ.setdefault("DATABASE_URL", "sqlite:///./test-prom-alerts.db")

ROOT = Path(__file__).resolve().parents[1]
PROM_DIR = ROOT / "deploy" / "prometheus"
ALERTS = PROM_DIR / "alerts-stability-platform.yml"
SCENARIOS = PROM_DIR / "alerts-stability-platform.test.yml"

_TOKEN_RE = re.compile(
    r"(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\s*\{(?P<labels>[^}]*)\})?"
    r"(?:\s*\[[^\]]*\])?"
)
_LABEL_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=")


def _alert_exprs() -> list[tuple[str, str]]:
    data = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    exprs = [
        (rule["alert"], rule["expr"])
        for group in data.get("groups", [])
        for rule in group.get("rules", [])
        if "alert" in rule
    ]
    assert exprs, "alerts 文件未解析出任何 alert 规则"
    return exprs


def _selectors(expr: str) -> list[tuple[str, list[str]]]:
    """从本仓库用到的 PromQL 形态中提取 (指标名, 标签名列表)。

    跳过函数名（后随 ``(``），忽略标签块内部文本。
    """
    selectors: list[tuple[str, list[str]]] = []
    covered_until = -1
    for match in _TOKEN_RE.finditer(expr):
        if match.start() < covered_until:
            continue
        if match.group("labels") is not None:
            covered_until = match.end()
        name = match.group("name")
        if expr[match.end():].lstrip().startswith("("):
            continue
        labels = [m.group(1) for m in _LABEL_RE.finditer(match.group("labels") or "")]
        selectors.append((name, labels))
    return selectors


def _metric_index() -> tuple[dict[str, set[str]], set[str]]:
    """(样本名→标签名集合, 不可查询的基础名集合)。

    来源 = backend 指标注册表（无 DB/网络副作用）。带标签的指标在未实例化
    子序列时 ``collect()`` 不产出样本，故用注册表的 collector→names 映射 +
    ``_labelnames``；Histogram/Summary 的**基础名**被 client 一并列出但不是
    可查询序列（裸用会静默永不触发），单独收集供显式拒绝。
    """
    sys.path.insert(0, str(ROOT))
    import backend.core.metrics  # noqa: F401  导入即注册
    from prometheus_client import REGISTRY

    collector_to_names = getattr(REGISTRY, "_collector_to_names", None)
    assert collector_to_names, "prometheus_client 注册表私有结构变化，请更新本测试"
    index: dict[str, set[str]] = {}
    for collector, names in collector_to_names.items():
        label_names = set(getattr(collector, "_labelnames", ()) or ())
        for name in names:
            # 直方图 _bucket 额外允许 le（client 不把它列进 _labelnames）
            extra = {"le"} if name.endswith("_bucket") else set()
            index.setdefault(name, set()).update(label_names | extra)

    non_queryable = {
        base
        for base in (getattr(c, "_name", "") for c in collector_to_names)
        if base
        and any(f"{base}{suffix}" in index for suffix in ("_bucket", "_sum", "_count"))
    }
    assert index, "指标注册表为空——backend.core.metrics 未注册任何指标"
    return index, non_queryable


def test_selector_parser_skips_functions_and_reads_labels():
    """解析器自证：避免结构层因解析退化为空而假绿。"""
    assert _selectors('increase(stability_a_total{outcome="failed"}[15m]) > 0') == [
        ("stability_a_total", ["outcome"]),
    ]
    assert _selectors("histogram_quantile(0.95, rate(stability_b_bucket[10m]))") == [
        ("stability_b_bucket", []),
    ]


def test_alert_selectors_match_metric_registry():
    index, non_queryable = _metric_index()
    problems: list[str] = []
    for alert, expr in _alert_exprs():
        selectors = _selectors(expr)
        assert selectors, f"{alert}: 未解析出选择器（解析器或表达式形态变化）"
        for name, labels in selectors:
            if name in non_queryable:
                problems.append(
                    f"{alert}: {name} 是 Histogram/Summary 基础名，须用 _bucket/_count/_sum"
                )
                continue
            if name not in index:
                problems.append(f"{alert}: 未知指标 {name}")
                continue
            unknown = sorted(set(labels) - index[name])
            if unknown:
                problems.append(f"{alert}: {name} 上未知标签 {unknown}")
    assert not problems, "告警选择器与指标注册表不一致：\n" + "\n".join(problems)


@pytest.mark.skipif(
    shutil.which("promtool") is None,
    reason="promtool 不可用；结构层校验已覆盖指标/标签契约",
)
def test_alert_scenarios_fire_with_promtool():
    checked = subprocess.run(
        ["promtool", "check", "rules", ALERTS.name],
        cwd=PROM_DIR, capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr

    result = subprocess.run(
        ["promtool", "test", "rules", SCENARIOS.name],
        cwd=PROM_DIR, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
