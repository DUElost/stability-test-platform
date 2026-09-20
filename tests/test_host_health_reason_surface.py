"""#2900/#2957：host 健康 reason 词表的四处口径必须一致（恒跑结构守卫）。

**本质问题**：一条 reason 从产生到响铃要穿过四个地方，没有任何东西约束它们相等——

1. agent 产出点（`capacity_reporter._compute_health` 的 `reasons.append(...)` 字面量，
   以及 `kernel_usb_faults` 的 `REASON_*` 常量）；
2. 控制面分桶词表（`api/routes/metrics.py` 的 `_HEALTH_REASONS`）——决定这条 reason
   有没有 series；
3. 告警规则里的标签选择器（`reason="..."`）——决定它有没有人响；
4. 前端 `REASON_LABELS`（`ExpandableHostTable.tsx`）——决定页面上是中文还是裸键。

每一处的失效都是**静默**的：②漏一个值 ⇒ 该 reason 永远没有 series，告警恒绿（#2237
那一类「定义了没人产」的镜像：「产了没人指标化」）；③拼错一个值 ⇒ 选择器指向一个
不存在的标签值，同样恒绿，而这条链的失败模式恰恰是 #1257 已经烧过一次的那一种；
④漏一个键 ⇒ 运维在页面上看到 `usb_tree_empty` 这种字串，得先猜它什么意思。
#2900 的三例失明（最长 11 天零告警）就是 ①②③ 全断的合取。

**判据**：以 agent 源码为唯一真值，双向对拍其余三处。②③用 AST/正则读源文件，
**不 import** `backend.*`（本目录是仓库级治理套件，不起 FastAPI/DB）。

`other` 是 ②的兜底桶，**不要求** agent 产出它（它存在的意义正是「agent 先发了新值」）；
因此对拍方向是 `agent 词表 ⊆ ②词表` 且 `②词表 - {other} ⊆ agent 词表`。

**判别力自证**：`test_guard_detects_*` 三条各造一个变异输入喂给同一个抽取/比对函数，
要求当场被抓——不写自证的守卫测不出「抽取器退化」（本仓 S10/#2914 的既定口径）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CAPACITY_REPORTER = REPO_ROOT / "backend/agent/capacity_reporter.py"
KERNEL_USB_FAULTS = REPO_ROOT / "backend/agent/kernel_usb_faults.py"
API_METRICS = REPO_ROOT / "backend/api/routes/metrics.py"
RULES = REPO_ROOT / "deploy/prometheus/alerts-stability-platform.yml"
FRONTEND_TABLE = REPO_ROOT / "frontend/src/components/network/ExpandableHostTable.tsx"

#: 控制面兜底桶（agent 不产出，见模块文档）。
CATCH_ALL_REASON = "other"


# ── 真值：agent 侧 ─────────────────────────────────────────────────────────
def agent_reasons_from_source(source: str) -> set[str]:
    """`reasons.append("x")` 形态的字面量（`_compute_health` 的产出方式）。"""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "append"):
            continue
        target = func.value
        if not (isinstance(target, ast.Name) and target.id == "reasons"):
            continue
        if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            found.add(node.args[0].value)
    return found


def module_str_constants(source: str, prefix: str) -> set[str]:
    """模块级 `PREFIX_NAME = "字符串"` 的值集合（`REASON_*` / `CHANNEL_*`）。"""
    tree = ast.parse(source)
    out: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = [t for t in node.targets if isinstance(t, ast.Name)]
        if any(n.id.startswith(prefix) for n in names) and isinstance(
            node.value, ast.Constant
        ) and isinstance(node.value.value, str):
            out.add(node.value.value)
    return out


def _tuple_literal(source: str, name: str) -> tuple[str, ...]:
    """读 `NAME = (…)` 的字符串元组（控制面词表）。"""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            assert isinstance(node.value, ast.Tuple), f"{name} 不再是元组字面量"
            values = [e.value for e in node.value.elts]
            assert all(isinstance(v, str) for v in values), f"{name} 含非字符串元素"
            return tuple(values)  # type: ignore[arg-type]
    raise AssertionError(f"{API_METRICS} 里找不到 {name}——词表搬家了就把本文件一起改")


def agent_reasons() -> set[str]:
    return agent_reasons_from_source(CAPACITY_REPORTER.read_text(encoding="utf-8")) | (
        module_str_constants(KERNEL_USB_FAULTS.read_text(encoding="utf-8"), "REASON_")
    )


def agent_channel_states() -> set[str]:
    """`CHANNEL_STATES` 元组的值（元素写成常量名，故需按模块常量解析一层）。"""
    tree = ast.parse(KERNEL_USB_FAULTS.read_text(encoding="utf-8"))
    consts = module_str_constants(KERNEL_USB_FAULTS.read_text(encoding="utf-8"), "CHANNEL_")
    names: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Constant):
            continue
        for t in node.targets:
            if isinstance(t, ast.Name) and isinstance(t.id, str) and isinstance(node.value.value, str):
                names[t.id] = node.value.value
    states: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CHANNEL_STATES" for t in node.targets
        ):
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    states.add(elt.value)
                elif isinstance(elt, ast.Name) and elt.id in names:
                    states.add(names[elt.id])
                else:
                    raise AssertionError(f"CHANNEL_STATES 含无法解析的元素：{ast.dump(elt)}")
    assert states, "kernel_usb_faults 里 CHANNEL_STATES 取不到——常量表搬家了？"
    assert states <= consts, f"CHANNEL_STATES 引用了非 CHANNEL_* 常量：{sorted(states - consts)}"
    return states


# ── 消费方 ②③④ ────────────────────────────────────────────────────────────
def control_plane_reasons() -> set[str]:
    return set(_tuple_literal(API_METRICS.read_text(encoding="utf-8"), "_HEALTH_REASONS"))


def _rule_selector_values(metric: str, label: str) -> dict[str, set[str]]:
    """告警规则里 `metric{label="v"}` 的选择器值：告警名 → 值集合。"""
    doc = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    pattern = re.compile(
        re.escape(metric) + r"\s*\{[^}]*\b" + re.escape(label) + r'="([^"]+)"'
    )
    out: dict[str, set[str]] = {}
    for group in doc.get("groups") or []:
        for item in group.get("rules") or []:
            if "alert" not in item:
                continue
            values = set(pattern.findall(str(item.get("expr", ""))))
            if values:
                out.setdefault(str(item["alert"]), set()).update(values)
    return out


def frontend_reason_labels(source: str) -> set[str]:
    """前端 `const REASON_LABELS: Record<string, string> = { key: '…' }` 的键。"""
    m = re.search(r"const REASON_LABELS[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert m, "ExpandableHostTable.tsx 里找不到 REASON_LABELS——前端改名了？"
    return set(re.findall(r"(?m)^\s*([a-z_][a-z0-9_]*)\s*:", m.group(1)))


# ── 断言 ───────────────────────────────────────────────────────────────────
def reason_vocabulary_is_not_hollow() -> set[str]:
    """反空转：抽取器退化时（agent 改了写法）后面每条断言都会「两边都空 → 绿」。"""
    reasons = agent_reasons()
    assert len(reasons) >= 8, f"agent 侧 reason 词表异常偏小：{sorted(reasons)}"
    return reasons


def test_control_plane_buckets_match_agent_vocabulary() -> None:
    reasons = reason_vocabulary_is_not_hollow()
    buckets = control_plane_reasons()
    missing = reasons - (buckets - {CATCH_ALL_REASON})
    stale = (buckets - {CATCH_ALL_REASON}) - reasons
    assert not missing, (
        f"这些 agent reason 没有分桶，永远不会进 /metrics（也就永远不会被告警）：{sorted(missing)}\n"
        f"补进 {API_METRICS.relative_to(REPO_ROOT)} 的 _HEALTH_REASONS。"
    )
    assert not stale, (
        f"_HEALTH_REASONS 里这些值 agent 已不产出（恒 0 的死 series）：{sorted(stale)}"
    )


def test_alert_rule_reason_selectors_are_real_values() -> None:
    selectors = _rule_selector_values("stability_host_health_reason", "reason")
    assert selectors, "没有任何告警规则引用 stability_host_health_reason——指标化白做了"
    buckets = control_plane_reasons()
    bad = {
        f"{alert}: {sorted(vals - buckets)}"
        for alert, vals in selectors.items()
        if vals - buckets
    }
    assert not bad, (
        "告警规则的选择器指向了词表外的 reason 值（PromQL 恒空 ⇒ 该告警永不触发）：\n  "
        + "\n  ".join(sorted(bad))
    )


def test_alert_rule_channel_selectors_are_real_values() -> None:
    selectors = _rule_selector_values("stability_host_kernel_log_channel", "state")
    assert selectors, "没有任何告警规则引用 stability_host_kernel_log_channel"
    states = set(_tuple_literal(API_METRICS.read_text(encoding="utf-8"), "_KERNEL_LOG_STATES"))
    assert states == agent_channel_states(), "控制面通道词表与 agent 的 CHANNEL_STATES 不一致"
    consts = module_str_constants(
        KERNEL_USB_FAULTS.read_text(encoding="utf-8"), "CHANNEL_"
    )
    assert consts == states, (
        f"kernel_usb_faults 里有 CHANNEL_* 常量未进 CHANNEL_STATES："
        f"{sorted(consts - states)}（漏了一个 ⇒ 该态永不可见）"
    )
    bad = {a: sorted(v - states) for a, v in selectors.items() if v - states}
    assert not bad, f"通道告警引用了未知 state 值：{bad}"


def test_frontend_labels_cover_agent_vocabulary() -> None:
    labels = frontend_reason_labels(FRONTEND_TABLE.read_text(encoding="utf-8"))
    missing = agent_reasons() - labels
    assert not missing, (
        f"这些 reason 在页面上会显示成裸键（运维得先猜含义）：{sorted(missing)}\n"
        f"补进 {FRONTEND_TABLE.relative_to(REPO_ROOT)} 的 REASON_LABELS。"
    )


def test_reason_gauge_is_refreshed_on_the_scrape_path() -> None:
    """两个 gauge 必须在 `/metrics` 处理函数里被调用。

    #2237 追的是「指标有没有生产者」；这里追的是更深一跳——**生产者在不在抓取路径上**。
    写在别的函数里、忘了挂进 handler，指标就恒不存在，比没有指标更坏（它会让
    「查了没有」看起来像「一切正常」）。
    """
    source = API_METRICS.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "metrics":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name):
                    called.add(inner.func.id)
    assert "_refresh_host_health_gauges" in called, (
        "/metrics 处理函数没有调用 _refresh_host_health_gauges——reason 指标恒缺失"
    )


def test_gauge_names_are_registered_in_core_metrics() -> None:
    source = (REPO_ROOT / "backend/core/metrics.py").read_text(encoding="utf-8")
    for name in ("stability_host_health_reason", "stability_host_kernel_log_channel"):
        assert f"'{name}'" in source, f"{name} 不在 core/metrics.py 注册表里"


# ── 判别力自证（每条都造一个真实的回归形状）──────────────────────────────
def test_guard_detects_bucket_missing_a_new_agent_reason() -> None:
    mutated = 'def _compute_health():\n    reasons = []\n    reasons.append("brand_new_fault")\n'
    extracted = agent_reasons_from_source(mutated)
    assert "brand_new_fault" in extracted
    assert "brand_new_fault" not in control_plane_reasons(), "变异名意外撞进了词表"
    # 与真断言同一套比对逻辑：新 reason 不在桶里 ⇒ 该红
    assert extracted - (control_plane_reasons() - {CATCH_ALL_REASON})


def test_guard_detects_stale_bucket_and_bad_selector_value() -> None:
    buckets = control_plane_reasons()
    assert "usb_host_controller_dead" in buckets
    assert "this_reason_no_longer_exists" not in agent_reasons()
    # ③的失配形状：规则选择了词表外的值 ⇒ 该红
    selectors = _rule_selector_values("stability_host_health_reason", "reason")
    for alert, vals in selectors.items():
        assert vals <= buckets, f"{alert} 的选择器值不在词表里却仍挂在规则上"


def test_guard_detects_frontend_label_gap() -> None:
    source = FRONTEND_TABLE.read_text(encoding="utf-8")
    labels = frontend_reason_labels(source)
    assert "usb_host_controller_dead" in labels
    # 抽掉一行就是回归形状（本轮实测抓到 `usb_tree_empty` 缺标签，见 PR Note）
    stripped = re.sub(r"(?m)^\s*usb_tree_empty\s*:[^\n]*\n", "", source)
    if "usb_tree_empty:" in source:
        assert "usb_tree_empty" not in frontend_reason_labels(stripped)
        assert agent_reasons() - frontend_reason_labels(stripped)
