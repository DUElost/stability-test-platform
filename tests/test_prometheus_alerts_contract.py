"""Prometheus 告警规则契约（#1257 / R14-F11）。

两层校验：

1. **结构层（恒跑）**：规则表达式的指标名与标签名必须存在于
   ``backend/core/metrics.py`` 的注册表——未知指标、未知标签、直方图裸用
   基础名（如 ``stability_x_observed`` 而非 ``_count/_bucket/_sum``）都拦。
   2026-09-10 审计发现的三条漂移（``status``/``job`` 标签、直方图裸名）由
   这一层机械拦截。
2. **场景层（promtool）**：``promtool test rules`` 用真实标签形状的样本证明
   修复过的规则可触发，覆盖阈值 / ``for:`` 时间窗 / 注解逐字匹配。可用性按
   job 分档（#2151）：夜间全量 ``backend-test`` 装了 pinned promtool 且注入
   ``PROMTOOL_REQUIRED=1`` → **缺失即红，不再静默 skip**；PR 路径与本机未装时
   显式 skip（PR 门禁不引入第三方二进制依赖，离线纯度见
   ``tests/test_offline_subset_guard.py``）。接线契约见
   ``tests/test_ci_promtool_scenario_gate.py``。
3. **覆盖棘轮（恒跑）**：promtool **只校验场景文件里出现过的告警**，未列规则改阈值
   它也不红（#2151 实测）。所以「新增告警必须带场景用例」这条只能在结构层成立——
   见 ``test_every_alert_rule_has_scenario_case`` 与 ``_SCENARIO_COVERAGE_DEBT``。

场景文件的 ``input_series`` 也走结构层（``test_scenario_input_series_match_metric_registry``，
#2152 折叠自 #2144）：CI 没有 promtool，那一层恒 skip，样本名字/标签错了就只能靠这里拦住。
同一事实不留两套标准——#2144 那个文件是本结构层的子集（不看标签），已删除。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.metrics_registry import metric_registry_index

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

# #1927：聚合前缀（`sum by (mode) (...)` 等）——先剥掉再扫 token，
# 否则 sum/by/mode 会被误当成指标名/标签选择器。
_AGG_PREFIX_RE = re.compile(
    r"\b(sum|avg|min|max|count|stddev|stdvar|topk|bottomk|quantile)\s+"
    r"(?:by|without)\s*\([^)]*\)\s*",
)

# #2030：聚合子句单独提取（`sum by (mode) (...)` → 标签列表 + 内层指标）——
# 剥离只能让解析器不误报，聚合标签本身必须另有校验，否则「指标改了标签、
# 告警没跟」（#1927 收敛 plan_run_id 时暴露的那一类）会静默通过。
_AGG_CLAUSE_RE = re.compile(
    r"\b(?P<fn>sum|avg|min|max|count|stddev|stdvar|topk|bottomk|quantile)\s+"
    r"(?P<mode>by|without)\s*\((?P<labels>[^)]*)\)",
)


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

    跳过函数名（后随 ``(``）与聚合前缀（``sum by (mode)``），忽略标签块内部文本。
    聚合前缀里的标签不在此处校验，由
    ``test_alert_aggregation_labels_match_metric_registry`` 单独负责（#2030）。
    """
    selectors: list[tuple[str, list[str]]] = []
    covered_until = -1
    stripped = _AGG_PREFIX_RE.sub("( ) ", expr)
    for match in _TOKEN_RE.finditer(stripped):
        if match.start() < covered_until:
            continue
        if match.group("labels") is not None:
            covered_until = match.end()
        name = match.group("name")
        if stripped[match.end():].lstrip().startswith("("):
            continue
        labels = [m.group(1) for m in _LABEL_RE.finditer(match.group("labels") or "")]
        selectors.append((name, labels))
    return selectors


def _aggregation_clauses(expr: str) -> list[tuple[str, str, list[str], str]]:
    """提取聚合子句 → ``[(聚合函数, by|without, 标签列表, 内层指标名)]``。

    内层指标名 = 子句之后第一个非函数名 token（跳过 ``increase(...)`` 等
    函数包装）。仅覆盖本仓库用到的形态（单层聚合 + 函数包裹）；定位不到
    内层指标的形态由契约用例显式报 problem（提醒更新本解析器），不静默放过。
    """
    clauses: list[tuple[str, str, list[str], str]] = []
    for m in _AGG_CLAUSE_RE.finditer(expr):
        labels = [x.strip() for x in m.group("labels").split(",") if x.strip()]
        rest = expr[m.end():]
        inner = ""
        for tok in _TOKEN_RE.finditer(rest):
            if rest[tok.end():].lstrip().startswith("("):
                continue  # 函数名，继续找其参数里的指标
            inner = tok.group("name")
            break
        clauses.append((m.group("fn"), m.group("mode"), labels, inner))
    return clauses


def test_selector_parser_skips_functions_and_reads_labels():
    """解析器自证：避免结构层因解析退化为空而假绿。"""
    assert _selectors('increase(stability_a_total{outcome="failed"}[15m]) > 0') == [
        ("stability_a_total", ["outcome"]),
    ]
    assert _selectors("histogram_quantile(0.95, rate(stability_b_bucket[10m]))") == [
        ("stability_b_bucket", []),
    ]


def test_aggregation_parser_reads_labels_and_inner_metric():
    """聚合解析器自证：防聚合标签校验因解析退化而空跑。"""
    assert _aggregation_clauses(
        "sum by (mode) (increase(stability_a_total[1h])) > 0"
    ) == [("sum", "by", ["mode"], "stability_a_total")]
    assert _aggregation_clauses("sum by (a, b) (rate(stability_b_total[5m]))") == [
        ("sum", "by", ["a", "b"], "stability_b_total"),
    ]
    # 无 by/without 的聚合不产出子句（没有聚合标签可校验）
    assert _aggregation_clauses("sum(rate(stability_c_total[5m]))") == []


def test_alert_selectors_match_metric_registry():
    index, non_queryable = metric_registry_index()
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


# promtool 的 ``input_series`` 形状是 ``name{k="v",…}``。**不复用** ``_selectors``：
# 那里的标签块用 ``[^}]*`` 截断，而本仓库的 endpoint 标签值是**路由模板**
# （``endpoint="/api/v1/jobs/{id}/complete"``）——值里的 ``{id}`` 会让 `}` 提前闭合，
# 把 ``complete`` / ``status_code`` 误读成指标名。故按该形状单写一个解析器，
# 配合下面的自证用例与「解析不出即报错」判据，不允许静默放过。
_SERIES_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\s*(?:\{(?P<labels>.*)\})?\s*$"
)
_SERIES_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"([^"]*)"')


def _series_selector(text: str) -> tuple[str, list[str]]:
    """单条 ``input_series`` → ``(指标名, 标签名列表)``；形态变化时**显式报错**。"""
    match = _SERIES_RE.match(text.strip())
    assert match is not None, (
        f"无法解析场景输入序列 {text!r}——写法变化需同步 _SERIES_RE，"
        "否则本守卫会静默失去覆盖"
    )
    labels = [m.group(1) for m in _SERIES_LABEL_RE.finditer(match.group("labels") or "")]
    return match.group("name"), labels


def _scenario_input_series() -> list[str]:
    """promtool 场景文件里所有 ``series:`` 输入序列（字符串形态 ``name{a="b"}``）。"""
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "series" and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return found


def test_series_selector_parser_self_proof():
    """场景序列解析器自证：含 ``{id}`` 的 endpoint 值不得被截断。

    这条正是本单折叠时踩到的形态：复用 ``_selectors`` 会把
    ``endpoint="/api/v1/jobs/{id}/complete"`` 之后的 ``complete`` 当成指标名。
    """
    assert _series_selector('stability_api_requests_total{method="POST",'
                            'endpoint="/api/v1/jobs/{id}/complete",status_code="404"}') == (
        "stability_api_requests_total", ["method", "endpoint", "status_code"])
    assert _series_selector("stability_patrol_failure_streak_observed_count") == (
        "stability_patrol_failure_streak_observed_count", [])
    assert _series_selector('stability_patrol_failure_streak_observed_bucket{le="+Inf"}') == (
        "stability_patrol_failure_streak_observed_bucket", ["le"])


def test_scenario_input_series_match_metric_registry():
    """场景文件的**输入序列**也要对上注册表（#2152 折叠自 #2144，并校验标签）。

    ``#2144`` 的 ``tests/test_prometheus_alert_metric_names.py`` 与本结构层校验同一条事实
    （规则 ↔ 注册表），却是它的子集（不看标签），唯一新增点是场景文件的 ``series:``——
    故折叠到这里，只保留那一条新增点。

    为什么值得单独校验：CI runner 没有 promtool，``test_alert_scenarios_fire_with_promtool``
    恒 skip，场景文件因此**从不被真正执行**。它的输入序列若指向不存在的指标或标签，
    规则永远匹配不到样本——"这条告警可触发"的证据其实是空转，与 ``#787`` / ``#2089``
    同源（同一事实的两处独立声明，且「没有告警」看起来与「一切正常」一样）。
    """
    index, non_queryable = metric_registry_index()
    series_list = _scenario_input_series()
    assert series_list, "场景文件未解析出任何 input_series——本用例会因解析退化而空跑"

    problems: list[str] = []
    derived_seen: list[str] = []
    for series in series_list:
        name, labels = _series_selector(series)
        if name.endswith(("_bucket", "_count", "_sum")):
            derived_seen.append(name)
        if name in non_queryable:
            problems.append(f"{series}: {name} 是 Histogram/Summary 基础名，须用派生序列")
            continue
        if name not in index:
            problems.append(f"{series}: 未知指标 {name}")
            continue
        unknown = sorted(set(labels) - index[name])
        if unknown:
            problems.append(f"{series}: {name} 上未知标签 {unknown}")
    # 派生序列的 `le` 放行分支必须真的被用到，否则该 allowance 是死代码
    assert derived_seen, (
        "场景文件没有任何 _bucket/_count/_sum 输入——直方图告警的样本形状已不受本用例覆盖"
    )
    assert not problems, (
        "promtool 场景输入序列与指标注册表不一致：\n" + "\n".join(problems)
        + "\n（CI 无 promtool，该文件从不真跑；名字/标签错了只会让场景证据空转）"
    )


def test_alert_aggregation_labels_match_metric_registry():
    """#2030：`sum by (...)` / `without (...)` 的聚合标签必须来自被聚合指标。

    原实现把聚合前缀整体剥掉（`_AGG_PREFIX_RE`），`by (...)` 里的标签不参与
    校验——「指标改了标签、告警没跟」会静默通过（#1927 收敛 plan_run_id 时
    暴露的正是这一类：指标侧已去 label、告警与文档侧滞后）。
    """
    index, _non_queryable = metric_registry_index()
    problems: list[str] = []
    seen: list[str] = []
    for alert, expr in _alert_exprs():
        for fn, mode, labels, inner in _aggregation_clauses(expr):
            seen.append(f"{alert}:{fn} {mode}")
            if not labels:
                continue
            if not inner or inner not in index:
                problems.append(
                    f"{alert}: {fn} {mode} (...) 未定位到内层指标（inner={inner!r}）"
                )
                continue
            unknown = sorted(set(labels) - index[inner])
            if unknown:
                problems.append(
                    f"{alert}: {fn} {mode} {unknown} 不在 {inner} 标签集 "
                    f"{sorted(index[inner])}"
                )
    assert seen, "告警表达式未解析出任何聚合子句（解析器或表达式形态变化）"
    assert not problems, "告警聚合标签与指标注册表不一致：\n" + "\n".join(problems)


_CMP_THRESHOLD_RE = re.compile(
    r"(?P<op>[<>]=?|==|=)\s*(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)",
)
_HUGE_THRESHOLD = "1e12"


def _promtool_required() -> bool:
    """本 job 是否把 promtool 当必备依赖（由 ci.yml 注入 ``PROMTOOL_REQUIRED``）。

    取值放宽成「非空且不是显式假值」而不是只认 ``"1"``：只认字面量会让
    ``PROMTOOL_REQUIRED: "true"`` 这类改写**静默退回 skip**——正是本单要修的失效模式。
    ci.yml 侧的取值形态由 ``tests/test_ci_promtool_scenario_gate.py`` 双向守住。
    """
    return os.environ.get("PROMTOOL_REQUIRED", "").strip().lower() not in {"", "0", "false"}


def _promtool_path() -> str:
    """promtool 路径；不可用时按 job 档位分流 **fail / skip**（#2151）。

    档位判据是 ci.yml 注入的 ``PROMTOOL_REQUIRED``（只在全量 ``backend-test`` 的
    ``Run repo-level tests`` 步骤出现）：

    - 该 job 里 promtool 缺失 = 安装步骤被删/失败/装错版本。此前它**恒 skip**，
      于是「阈值或时间窗改了、场景文件没跟」这类语义漂移只在装了 promtool 的
      机器被拦（= 取决于跑测机器）——正是 #2151 要消灭的盲区，这里不放行；
    - PR 路径与开发机未装 promtool 仍是 skip：结构层恒跑已覆盖指标/标签契约，
      而 PR 门禁不能引入第三方二进制依赖。

    两条出口都带可诊断文本，不静默。
    """
    path = shutil.which("promtool")
    if path:
        return path
    if _promtool_required():
        pytest.fail(
            f"PROMTOOL_REQUIRED={os.environ.get('PROMTOOL_REQUIRED')!r} 但 PATH 中没有 "
            "promtool：ci.yml 的「Install pinned promtool」步骤缺失或失败——场景层静默 "
            "skip 是 #2151 要修的盲区，全量 job 不放行"
        )
    pytest.skip("promtool 不可用；结构层校验已覆盖指标/标签契约")
    raise AssertionError("unreachable：pytest.fail / pytest.skip 均已抛出")


def _promtool_version(promtool: str) -> str:
    out = subprocess.run(
        [promtool, "--version"], capture_output=True, text=True, check=False
    )
    return (out.stdout or out.stderr).splitlines()[0] if (out.stdout or out.stderr) else "未知"


def test_alert_scenarios_fire_with_promtool():
    promtool = _promtool_path()
    checked = subprocess.run(
        [promtool, "check", "rules", ALERTS.name],
        cwd=PROM_DIR, capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr

    result = subprocess.run(
        [promtool, "test", "rules", SCENARIOS.name],
        cwd=PROM_DIR, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # 夜间日志可读性：跑的是哪个版本（#2151 验收判据①的另一半；安装步骤也打印）
    print(f"[promtool] {_promtool_version(promtool)} 场景层 {SCENARIOS.name} → SUCCESS")


def _raise_all_thresholds(alerts_text: str) -> tuple[str, int]:
    """把每条**告警**规则的比较阈值统一抬到 ``1e12``，返回 (新文本, 命中数)。

    只做单向抬高（永不放宽），因此变异后的规则严格更难触发：场景文件里
    「某时刻应出现该告警」的断言必然失配。录制规则（无 ``alert`` 键）不动。
    """
    data = yaml.safe_load(alerts_text)
    hits = 0
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            expr = rule.get("expr")
            if "alert" not in rule or not isinstance(expr, str):
                continue
            mutated, n = _CMP_THRESHOLD_RE.subn(
                lambda m: f"{m.group('op')} {_HUGE_THRESHOLD}", expr
            )
            if n:
                rule["expr"] = mutated
                hits += n
    return yaml.safe_dump(data, allow_unicode=True, width=10000), hits


# 场景层只校验场景文件里出现过的告警：**promtool 对未列规则不校验**。
# #2151 实测——把 StabilityPlanRunAggregationFailed 的 `> 0` 改成 `> 500`、
# 场景文件不跟，`promtool test rules` 仍 SUCCESS。所以「装了 promtool」只把
# 已列规则从「取决于跑测机器」变成「常量」，未列规则依然零覆盖。
# 于是「新增告警必须带场景」必须落在**恒跑结构层**（PR 路径即拦）。
#
# 下面 10 条是本单落地时已存在的存量缺口（各规则加入时场景层在 CI 恒 skip，
# 漏补无人可见）。清单只准缩短不准变长：补了场景却没删条目 → 红；
# 加了新告警不补场景 → 红。逐条补齐 = #2236（背景与判据见
# docs/notes/testing/2026-09-16-promtool-nightly-scenario-gate-2151.md）；
# 「指标有定义但无生产者」是另一条轴，另单 #2237 负责。
_SCENARIO_COVERAGE_DEBT: frozenset[str] = frozenset({
    "StabilityPlanRunAggregationFailed",
    "StabilityPostCompletionEnqueueFailed",
    "StabilityClaimLeaseFailedSpike",
    "StabilityCsrfOriginRejected",
    "StabilityCsrfMissingOriginReferer",
    "StabilityDispatchGateSlow",
    "StabilitySaqQueueDepth",
    "StabilityMergeSkipToolNotConfigured",
    "StabilityUnlinkedFixable",
    "StabilityDbDeadlockDetected",
})


def _scenario_alert_names() -> set[str]:
    data = yaml.safe_load(SCENARIOS.read_text(encoding="utf-8"))
    return {
        case.get("alertname")
        for group in data.get("tests", [])
        for case in (group.get("alert_rule_test") or [])
        if case.get("alertname")
    }


def test_every_alert_rule_has_scenario_case():
    """告警规则 ↔ 场景用例的覆盖棘轮（#2151 验收判据② 的可成立版本）。

    四条断言各自堵一种「清单说谎」：

    - 场景引用了已不存在的告警 → promtool 不报错，但断言的是空气；
    - 新告警不补场景 → 漂移不可见（本单实测过的真实缺口）；
    - 存量债条目对应的规则被删 → 清单继续挂着不存在的名字，后来者无法判断可信度；
    - 存量债条目已补场景 → 清单只准缩短，缩短就必须同步（否则「还有 10 处待补」
      这个信息本身变成噪声）。
    """
    alerts = {name for name, _expr in _alert_exprs()}
    covered = _scenario_alert_names()
    assert covered <= alerts, f"场景文件引用了不存在的告警：{sorted(covered - alerts)}"
    new_gaps = sorted(alerts - covered - _SCENARIO_COVERAGE_DEBT)
    assert not new_gaps, (
        "以下告警规则没有 promtool 场景用例（promtool 对未列规则不校验 = 阈值/时间窗"
        f"漂移不可见）：{new_gaps}。请在 {SCENARIOS.name} 补 input_series + "
        "alert_rule_test；存量清单见 _SCENARIO_COVERAGE_DEBT"
    )
    vanished = sorted(_SCENARIO_COVERAGE_DEBT - alerts)
    assert not vanished, f"存量债清单里有已不存在的规则，请从清单删除：{vanished}"
    drained = sorted(_SCENARIO_COVERAGE_DEBT & covered)
    assert not drained, (
        f"这些规则已补场景用例，请同步从 _SCENARIO_COVERAGE_DEBT 删除：{drained}"
    )


def test_promtool_gate_detects_threshold_drift(tmp_path):
    """负向对照（#2151 验收判据②）：阈值漂移必须让场景层变红。

    为什么必须有：`test_alert_scenarios_fire_with_promtool` 只断言 rc == 0。接线一旦
    退化（子命令语义变化、场景文件不再引用规则文件、规则文件改名而 promtool 只是
    "没报错"），恒绿与恒红无法区分。本用例证明这对文件**有判别力**。

    先在同形沙箱里跑**未变异**副本作控制组——只有控制组绿，变异组的红才能归因于
    阈值本身，而不是临时目录/路径拼错导致的假红。
    """
    promtool = _promtool_path()
    original = ALERTS.read_text(encoding="utf-8")
    mutated, hits = _raise_all_thresholds(original)
    assert hits, (
        "未命中任何比较阈值：告警表达式形态已变，本负向对照失去判别力，"
        "请同步更新 _CMP_THRESHOLD_RE（不要删除本用例）"
    )

    def _run(alerts_text: str) -> subprocess.CompletedProcess[str]:
        (tmp_path / ALERTS.name).write_text(alerts_text, encoding="utf-8")
        (tmp_path / SCENARIOS.name).write_text(
            SCENARIOS.read_text(encoding="utf-8"), encoding="utf-8"
        )
        return subprocess.run(
            [promtool, "test", "rules", SCENARIOS.name],
            cwd=tmp_path, capture_output=True, text=True, check=False,
        )

    control = _run(original)
    assert control.returncode == 0, (
        f"控制组（未变异副本）应当通过——沙箱接线本身有问题：\n"
        f"{control.stdout}{control.stderr}"
    )
    drift = _run(mutated)
    assert drift.returncode != 0, (
        f"全部阈值抬高到 {_HUGE_THRESHOLD} 后场景层仍通过 → promtool 场景层是假绿"
        f"（{hits} 处阈值未产生任何失配）\n{drift.stdout}{drift.stderr}"
    )
    assert "FAILED" in drift.stdout + drift.stderr, (
        f"变异组红了但输出里没有 FAILED，失败原因可能不是场景失配"
        f"（例如文件路径错误）：\n{drift.stdout}{drift.stderr}"
    )
