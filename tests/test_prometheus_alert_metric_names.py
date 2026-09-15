"""#2144：告警表达式引用的指标名必须真的存在（防重命名后告警静默失效）。

背景：`deploy/prometheus/` 下的告警规则与 promtool 单测都靠**字符串**引用指标名，而指标名
在 `backend/core/metrics.py` 里声明。改名指标时，代码侧与 YAML 侧没有任何编译器或运行时能
拦住——告警会**静默地永不触发**，而「没有告警」看起来和「一切正常」一样（与 `#787`/`#2089`
两处漂移同源：同一事实的两处独立声明）。

纯离线：只读 YAML 与源码，不起服务、不连库（`tests/` 准入判据）。

判据除了「名字已声明」，还管**派生序列**：直方图/摘要会额外导出 `_bucket` / `_count` /
`_sum`，告警引用它们是合法的——**但只有当基名仍是直方图/摘要时**合法。只按下划线后缀放行、
不校验类型，就会漏掉「有人把直方图改成 Gauge」这种把告警变成永不触发的情形（本文件第一版
就是被自己这条检查抓到的：当时它对三个 `_bucket`/`_count` 引用误报缺失）。

覆盖边界：本检查保证「名字存在 + 派生序列的类型前提成立」，**不保证语义正确**（阈值、聚合、
时间窗的校验需要 `promtool test rules`；`deploy/prometheus/alerts-stability-platform.test.yml`
已经写好，但当前没有被 CI 执行——原因见该单 Note 的 Revisit）。

第二处边界：只查 `expr` / `series`，**annotations 里的散文不查**（散文允许在迁移期保留历史名，
这是刻意取舍）。代价是：若某指标只被散文提到（如 `StabilityDbLockWaitSustained` 的描述让值班
去看 `stability_db_lock_waiters`），改名后描述会指向不存在的指标而本检查不红。若这类「值班
指引漂移」成为问题，再考虑把散文纳入（那时需要一份允许保留历史名的白名单）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ALERTS = ROOT / "deploy" / "prometheus" / "alerts-stability-platform.yml"
PROMTOOL_TESTS = ROOT / "deploy" / "prometheus" / "alerts-stability-platform.test.yml"
METRICS = ROOT / "backend" / "core" / "metrics.py"

_METRIC_RE = re.compile(r"\bstability_[a-z0-9_]+")

# 只看这两类键下的字符串：`expr` 是告警表达式、`series` 是 promtool 单测的输入序列。
# annotations / labels 里的散文也常提到指标名，但那是给人看的、允许出现历史名字——把它们
# 一并扫进来会假红，所以按语义位置限缩。
_EXPR_KEYS = {"expr", "series"}

# 直方图/摘要导出的派生序列后缀，以及允许导出它们的构造器。
_DERIVED_SUFFIXES = ("_bucket", "_count", "_sum")
_DERIVED_PARENTS = {"Histogram", "Summary"}


def _declared_metrics() -> dict[str, str]:
    """`{指标名: 构造器名}`——`metrics.py` 里的声明（Metric 构造的第一个字符串参数）。

    类型要留着：派生序列的合法性取决于基名的类型（见模块 docstring）。
    """
    tree = ast.parse(METRICS.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        for arg in node.args:
            if (
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith("stability_")
            ):
                out[arg.value] = node.func.id
    return out


def _referenced_metric_names(path: Path) -> set[str]:
    """YAML 里 `expr:` / `series:` 值中出现的指标名。"""
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _EXPR_KEYS and isinstance(value, str):
                    found.update(_METRIC_RE.findall(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(yaml.safe_load(path.read_text(encoding="utf-8")))
    return found


def _unknown_references(path: Path) -> list[str]:
    declared = _declared_metrics()
    unknown: list[str] = []
    for name in sorted(_referenced_metric_names(path)):
        if name in declared:
            continue
        for suffix in _DERIVED_SUFFIXES:
            if name.endswith(suffix) and declared.get(name[: -len(suffix)]) in _DERIVED_PARENTS:
                break
        else:
            unknown.append(name)
    return unknown


def test_declared_metrics_are_discovered():
    """防解析失效把断言变成永真。"""
    names = _declared_metrics()
    assert len(names) >= 20, f"只解析出 {len(names)} 个指标名；解析逻辑或文件布局已改"
    assert any(kind == "Histogram" for kind in names.values()), (
        "一个直方图都没解析出来——派生序列分支会静默失效"
    )


def test_alert_files_are_discovered():
    for path in (ALERTS, PROMTOOL_TESTS):
        assert _referenced_metric_names(path), (
            f"{path.name} 未解析出任何指标引用——`expr`/`series` 的写法改动后本守卫会静默失效"
        )


def test_derived_series_branch_is_exercised():
    """派生序列分支当前**确实被用到**（有 `_bucket`/`_count` 引用）。

    若哪天不再被用到，这条会红——那时要复核该分支是否已成死代码（死分支会让守卫看起来比
    实际更强）。
    """
    declared = _declared_metrics()
    derived = [
        name
        for path in (ALERTS, PROMTOOL_TESTS)
        for name in _referenced_metric_names(path)
        if name not in declared and name.endswith(_DERIVED_SUFFIXES)
    ]
    assert derived, "当前没有任何派生序列引用——复核 _DERIVED_SUFFIXES 分支是否仍需要"


def test_referenced_metrics_are_declared():
    missing: dict[str, list[str]] = {}
    for path in (ALERTS, PROMTOOL_TESTS):
        unknown = _unknown_references(path)
        if unknown:
            missing[path.name] = unknown
    assert not missing, (
        f"以下告警/promtool 单测引用了未声明的指标名：{missing}——改名指标时告警会**静默**"
        "失效（不再触发，而「没有告警」看起来与「一切正常」一样）。请同步 "
        "`backend/core/metrics.py` 的声明与 `deploy/prometheus/` 下的引用。"
    )
