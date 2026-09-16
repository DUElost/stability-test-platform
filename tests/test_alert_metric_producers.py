"""#2237：告警引用的指标必须真的有生产者（恒跑结构守卫，PR 路径即拦）。

同类缺陷在两个面的覆盖度不一致：仪表板面早已由 #1258 建了 `UNPRODUCED_METRICS` 轴
（`tests/test_grafana_dashboard_contract.py`），告警面**零覆盖**。既有结构层
（`test_alert_selectors_match_metric_registry`）只校验「指标名在 `prometheus_client`
注册表里」——而**注册表里有定义 ≠ 有人调用**：Counter/Gauge 一旦在模块级构造就自动
入表。于是「引用死指标的告警」= 永不触发、永不误报、永不出红灯，静默失效；而告警
静默失效正是本仓反复踩的那一类（#1958 死锁四周零指标、#1257 不存在的标签选择器）。

判别力为什么不够（#2151 期间的真实经过）：按指标名
`stability_dispatch_gate_duration_seconds` grep `backend/` 只命中
`backend/core/metrics.py:441` 的定义行，差点把它判成死规则；真实埋点在 helper
`record_dispatch_gate`（`backend/core/metrics.py:693`）里，调用点
`backend/services/precheck/runner.py:359`。结论正确但**过程靠人肉追两跳**——
本文件就是把那两跳固化成断言。

判据（AST，不用正则 grep）：

1. 解析 `backend/**/*.py`（排除 `tests/`、已发布脚本目录、alembic、resources），
   收集 `X = Counter("stability_x", ...)` 形态的定义 → 指标名 ↔ 标识符；
2. **写入点** = mutator 调用链（`inc` / `observe` / `set` / `labels` / `remove` /
   `delete`）里出现该标识符，覆盖四种真实形态：
   - `foo_total.inc()`（直接导入后调用）
   - `metrics.unlinked_fixable_total.inc()`（模块属性访问）
   - `saq_queue_depth_gauge.labels(...).set(...)`（**别名导入**，
     `from backend.core.metrics import saq_queue_depth as saq_queue_depth_gauge`）
   - `csrf_rejected_total.labels(reason=reason).inc()`（labels 链）
3. 同文件内的写入不算生产者（那就是定义处）；此时**追一跳**：写入点若在
   `backend/core/metrics.py` 的某个 `record_*` helper 里，则该 helper 必须在异文件
   有调用点——这正是 `record_dispatch_gate` 那一类；
4. Histogram/Summary 的 `_bucket` / `_count` / `_sum` 后缀序列归一到基础名
   （告警按样本名查询，但代码里 observe 的是基础指标）；
5. **框架回调这一跳 AST 看不见**，另钉 `_FRAMEWORK_WIRED_METRICS`：写入点位于中间件
   `dispatch` 里的指标（`stability_api_requests_total` ← `ApiRequestMetricsMiddleware`），
   必须能在 `backend/main.py` 的 AST 里找到 `add_middleware(ApiRequestMetricsMiddleware)`
   调用。用 AST 而非子串是实测教训：子串匹配会被「把整行注释掉」骗过，D 组对照最初
   就是绿的。

判据边界（不是全量可达性证明）：只追**一跳**，且一跳的终点是「代码里存在调用点」。
「调用点本身是否会在运行时被走到」（分支永假、任务未注册、路由未挂载）不由本文件判定——
那要执行期证据。本文件消灭的是最省事的一类：定义了、门面也写了、但**没有任何人调门面**。

存量：#2151 已逐条核对 17 条告警引用的指标都有真实生产者，故允许清单为空
（新门禁落地不背存量，同 S14 口径）。将来确实无生产者却仍挂告警 → 必须进
`_ALERT_UNPRODUCED_ALLOWLIST` 且带原因与终态出口，不许无解释豁免。

纯离线：只读源码 + AST，不起容器、不连库、不调网络。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
ALERTS = REPO_ROOT / "deploy" / "prometheus" / "alerts-stability-platform.yml"

# 扫描根与排除项：只认「控制面/服务侧真实代码」里的埋点
_SCAN_ROOT = REPO_ROOT / "backend"
_EXCLUDE_PARTS = {"tests", "resources", "alembic", "__pycache__"}
# backend/agent/scripts/<name>/v<version>/ 是投递到设备执行的独立程序，不产中心指标
_EXCLUDE_PREFIX = "backend/agent/scripts/"

_CONSTRUCTORS = {"Counter", "Gauge", "Histogram", "Summary", "Info", "Enum"}
# prometheus_client 的写入方法（labels() 返回子序列，视为写入意图）
_MUTATORS = {"inc", "dec", "observe", "set", "labels", "remove", "delete"}
_HISTOGRAM_SUFFIXES = ("_bucket", "_count", "_sum", "_created")

# 有定义、暂无生产者的告警指标（同 #1258 的 UNPRODUCED_METRICS 模式）。
# 每条必须写原因 + 终态出口；空集就是当前期望值。
_ALERT_UNPRODUCED_ALLOWLIST: dict[str, str] = {}


def _iter_source_files(root: Path) -> list[tuple[str, Path, ast.Module]]:
    out: list[tuple[str, Path, ast.Module]] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(_EXCLUDE_PREFIX):
            continue
        if _EXCLUDE_PARTS & set(rel.split("/")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 语法错由 compileall 拦
            continue
        out.append((rel, path, tree))
    return out


def collect_definitions(files) -> dict[str, dict]:
    """收集 ``ident = Counter("stability_x", ...)`` 形态 → {指标名: {ident,file,kind,line}}。"""
    defs: dict[str, dict] = {}
    for rel, _path, tree in files:
        for node in ast.walk(tree):
            targets: list[ast.expr]
            value: ast.expr | None
            if isinstance(node, ast.Assign):
                targets, value = list(node.targets), node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets, value = [node.target], node.value
            else:
                continue
            if isinstance(value, ast.IfExp):  # `X = Counter(...) if PROMETHEUS_AVAILABLE else _Mock()`
                value = value.body
            if not isinstance(value, ast.Call) or not value.args:
                continue
            func = value.func
            kind = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if kind not in _CONSTRUCTORS:
                continue
            first = value.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    defs.setdefault(
                        first.value,
                        {"ident": target.id, "file": rel, "kind": kind, "line": node.lineno},
                    )
    return defs


def _chain(node: ast.AST) -> list[str]:
    """把调用/属性混合链摊平：``b.labels(q=1).set`` → ['b','labels','set']。

    必须穿过中间的 ``Call``：prometheus 的 labels 形态是
    ``x.labels(...).inc()``，``func`` 是 ``Attribute(value=Call(...))``，
    只走 Attribute/Name 会让最常见的一类写入点整体消失（实测漏掉 csrf 埋点）。
    """
    parts: list[str] = []
    cur: ast.AST | None = node
    while cur is not None:
        if isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        elif isinstance(cur, ast.Call):
            cur = cur.func
        elif isinstance(cur, ast.Name):
            parts.append(cur.id)
            return list(reversed(parts))
        else:
            return []
    return []


def _aliases(tree: ast.Module) -> dict[str, str]:
    """本文件的局部别名 → 原始名（``import x as y`` 与 ``from m import x as y``）。

    别名不还原就会漏判：`from backend.core.metrics import saq_queue_depth as
    saq_queue_depth_gauge` 之后写入用的是 `saq_queue_depth_gauge`，按标识符匹配
    什么都找不到（`backend/scheduler/app_scheduler.py:24` 就是这个形态）。
    """
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.asname:
                    out[alias.asname] = alias.name
    return out


def _calls(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    """所有调用点：[(点分链, 行号, 链各段)]（链已按别名还原首段）。"""
    alias = _aliases(tree)
    out: list[tuple[str, int, list[str]]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if not chain:
            continue
        parts = [alias.get(chain[0], chain[0])] + chain[1:]
        out.append((".".join(parts), node.lineno, parts))
    return out


def _mutator_calls(tree: ast.Module) -> list[tuple[str, int, list[str]]]:
    return [item for item in _calls(tree) if item[2][-1] in _MUTATORS]


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """行号 → 最内层所属函数名（把写入点归属到 `record_*` helper）。"""
    span: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for sub in ast.walk(fn):
                line = getattr(sub, "lineno", None)
                if line is not None:
                    span.setdefault(line, fn.name)
    return span


def _base_name(metric: str) -> str:
    """直方图/摘要样本名归一到基础名（``x_seconds_bucket`` → ``x_seconds``）。

    告警按样本名查询，代码里 observe 的是基础指标；不归一就会把每条直方图告警
    误判成「注册表外定义缺失」。
    """
    for suffix in _HISTOGRAM_SUFFIXES:
        if metric.endswith(suffix) and len(metric) > len(suffix):
            return metric[: -len(suffix)]
    return metric


def produced_map(files, defs: dict[str, dict]) -> dict[str, list[str]]:
    """基础指标名 → 生产者证据。两趟：跨文件直接写入 + 定义文件内 helper 一跳。

    「同文件的写入不算生产者」是刻意的：指标只被自己所在模块引用，通常意味着
    那是个 `record_*` 门面，真正的触发点在别处——不追这一跳就会误判（#dispatch gate
    那次的误判正是没追），而把同文件写入直接算成生产者又会放行「定义了、门面也写了、
    但没人调门面」的死指标。一跳解析把两者都覆盖。
    """
    ident_to_base = {d["ident"]: base for base, d in defs.items()}
    evidence: dict[str, list[str]] = {}
    helper_bases: dict[str, set[str]] = {}

    for rel, _path, tree in files:
        spans = _enclosing_functions(tree)
        for chain, line, parts in _mutator_calls(tree):
            base = next(
                (ident_to_base[name] for name in parts[:-1] if name in ident_to_base),
                None,
            )
            if base is None:
                continue
            if rel == defs[base]["file"]:
                helper_bases.setdefault(spans.get(line, "<module>"), set()).add(base)
                continue
            evidence.setdefault(base, []).append(f"{rel}:{line} {chain}()")

    # 一跳：定义文件内的 `record_*` helper，其调用点必须在别的文件
    call_index: dict[str, list[tuple[str, int, str]]] = {}
    for rel, _path, tree in files:
        for chain, line, parts in _calls(tree):
            call_index.setdefault(parts[-1], []).append((rel, line, chain))
    for helper, bases in helper_bases.items():
        if helper == "<module>":
            continue
        for rel, line, chain in call_index.get(helper, []):
            for base in bases:
                if rel == defs[base]["file"]:
                    continue
                evidence.setdefault(base, []).append(f"{rel}:{line} {chain}() -> {helper}")
    return evidence


def weak_reference_sites(files, defs: dict[str, dict]) -> dict[str, list[str]]:
    """跨文件出现该标识符、但没有任何 mutator 证据的位置（用于把假红变成可判读提示）。

    真实存在这一类：`backend/api/routes/metrics.py:43` 把 `device_online` /
    `host_online` 塞进 `_FLEET_GAUGES` 元组，再由 `_refresh_fleet_gauges` 遍历
    `gauge.set(...)` 写入——AST 上「指标标识符」和「写入调用」不在同一条链里，
    强判据看不见。判成死规则是假红；直接放宽成「跨文件出现即算生产者」又会放行
    「import 了但从不写」的死指标。折中：强判据不变，弱引用只作为**报错线索**，
    要转正必须进 `_MANUAL_WIRED_METRICS` 留下可核对的接线证据。
    """
    ident_to_base = {d["ident"]: base for base, d in defs.items()}
    sites: dict[str, list[str]] = {}
    for rel, _path, tree in files:
        alias = _aliases(tree)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names.append(alias.get(node.id, node.id))
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
            elif isinstance(node, ast.ImportFrom):
                names.extend(a.name for a in node.names)
            for name in names:
                base = ident_to_base.get(name)
                if base and rel != defs[base]["file"]:
                    sites.setdefault(base, []).append(f"{rel}:{getattr(node, 'lineno', 0)}")
    return sites


def alert_metric_names() -> list[str]:
    """告警表达式里引用的全部 ``stability_*`` 名字（含直方图样本名）。"""
    data = yaml.safe_load(ALERTS.read_text(encoding="utf-8"))
    names: list[str] = []
    for group in data.get("groups", []):
        for rule in group.get("rules", []):
            if "alert" not in rule:
                continue
            found = re.findall(r"\bstability_[a-zA-Z0-9_:]+\b", str(rule["expr"]))
            assert found, f"{rule['alert']}: 未解析出 stability_* 指标名（表达式形态变化）"
            names.extend(found)
    return sorted(set(names))


def _resolve(metric: str, defs: dict[str, dict]) -> str | None:
    """指标名 → defs 里的键（原样命中，或经直方图后缀归一命中）。"""
    if metric in defs:
        return metric
    return _base_name(metric) if _base_name(metric) in defs else None


def unproduced_alert_metrics() -> dict[str, str]:
    """返回 {告警引用的指标名: 失败原因}，只统计**告警引用到**的指标。"""
    files = _iter_source_files(_SCAN_ROOT)
    defs = collect_definitions(files)
    produced = produced_map(files, defs)
    weak = weak_reference_sites(files, defs)
    problems: dict[str, str] = {}
    for metric in alert_metric_names():
        base = _resolve(metric, defs)
        if base is None:
            problems[metric] = "非测试代码里找不到该指标的 Counter/Gauge/... 定义"
            continue
        if base in produced or base in _MANUAL_WIRED_METRICS:
            continue
        hints = ", ".join(weak.get(base, [])[:3])
        if hints:
            problems[metric] = (
                f"{defs[base]['file']}:{defs[base]['line']} 无直接写入点，但被跨文件引用"
                f"（{hints}）——疑似经容器/变量间接持有；确认接线后进 "
                f"_MANUAL_WIRED_METRICS 登记证据，别放宽本判据"
            )
        else:
            problems[metric] = (
                f"{defs[base]['file']}:{defs[base]['line']} 有定义、无任何写入点也无跨文件引用"
            )
    return problems


def _synthetic(tmp_path: Path):
    """把 tmp 合成树喂给分析器（rel 用文件名，不依赖 REPO_ROOT）。"""
    out = []
    for path in sorted(tmp_path.rglob("*.py")):
        try:
            out.append((path.name, path, ast.parse(path.read_text(encoding="utf-8"))))
        except SyntaxError:  # pragma: no cover - 合成代码语法错应立即暴露在本函数
            raise
    return out


# AST 看不见的接线，需要人工登记证据。不登记就会有两类后果：中间件忘了挂载时
# 告警静默失效却不红（太松）；或指标经容器间接写入时被误判成死指标（太紧，假红）。
# 每条 = 指标名 -> (文件, 调用名, 作为实参出现的对象名)。适用两类 AST 追不到的
# 接线：框架回调（中间件 dispatch 由 ASGI 调）、以及把指标对象塞进容器再遍历写入
# （`_FLEET_GAUGES` 那种 `for ..., gauge, ... in _FLEET_GAUGES: gauge.set(...)`）。
# 用 AST 而不是子串：实测子串匹配会被「把整行注释掉」骗过（D 组对照最初就是绿的）。
_MANUAL_WIRED_METRICS: dict[str, tuple[str, str, str]] = {
    "stability_api_requests_total": (
        "backend/main.py",
        "add_middleware",
        "ApiRequestMetricsMiddleware",
    ),
}


def _call_has_name_arg(tree: ast.Module, call_name: str, arg_name: str) -> bool:
    """是否存在 ``...<call_name>(..., <arg_name>, ...)`` 形态的真实调用。"""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        chain = _chain(node.func)
        if not chain or chain[-1] != call_name:
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Name) and arg.id == arg_name:
                return True
    return False


def test_framework_wired_alert_metrics_stay_wired():
    """`_MANUAL_WIRED_METRICS` 钉住的接线仍在场（且清单不长成噪声）。"""
    referenced = set(alert_metric_names()) | {_base_name(m) for m in alert_metric_names()}
    problems: list[str] = []
    for metric, (rel, call_name, arg_name) in sorted(_MANUAL_WIRED_METRICS.items()):
        if metric not in referenced:
            problems.append(f"{metric}: 已不被任何告警引用，请从清单删除")
            continue
        path = REPO_ROOT / rel
        if not path.exists():
            problems.append(f"{metric}: {rel} 不存在")
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if not _call_has_name_arg(tree, call_name, arg_name):
            problems.append(
                f"{metric}: {rel} 里已没有 {call_name}({arg_name}) 调用"
                f"（{arg_name} 的写入点因此不可达）"
            )
    assert not problems, "\n".join(problems)


def test_alert_metrics_all_have_producers():
    """告警不得引用「有定义、无生产者」的指标（除非带解释地进允许清单）。"""
    problems = unproduced_alert_metrics()
    unexplained = {m: why for m, why in problems.items() if m not in _ALERT_UNPRODUCED_ALLOWLIST}
    assert not unexplained, (
        "以下告警引用的指标查不到生产者，规则会永不触发（补埋点 / 撤规则 / "
        "带原因进 _ALERT_UNPRODUCED_ALLOWLIST）：\n"
        + "\n".join(f"  {m}: {why}" for m, why in sorted(unexplained.items()))
    )
    stale = sorted(set(_ALERT_UNPRODUCED_ALLOWLIST) - set(problems))
    assert not stale, f"允许清单里的指标已恢复生产者或已不在告警中，请删除：{stale}"


def test_producer_analyzer_is_discriminative(tmp_path):
    """判别器自证：同一棵合成树，有调用点→绿、删调用点→红。

    为什么必须有：守卫读的是 AST，判据一旦退化（mutator 名单写错、别名没还原、
    helper 一跳失效），它会**静默把一切判成无生产者**——那时靠允许清单放行就等于
    把门禁换成噪声。本用例用合成树证明两个方向都判得动。
    """
    (tmp_path / "metrics_mod.py").write_text(
        "from prometheus_client import Counter\n"
        "probe_total = Counter('stability_probe_total', 'p', ['who'])\n"
        "def record_probe():\n"
        "    probe_total.labels(who='x').inc()\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "from metrics_mod import record_probe\n"
        "def handle():\n"
        "    record_probe()\n",
        encoding="utf-8",
    )
    files = _synthetic(tmp_path)
    defs = collect_definitions(files)
    assert "stability_probe_total" in defs, "合成树都解析不出定义 → 判别器已失效"
    assert "stability_probe_total" in produced_map(files, defs), "helper 一跳形态应判为有生产者"

    # 删掉调用点：必须变成「无生产者」
    (tmp_path / "consumer.py").write_text("def handle():\n    return 1\n", encoding="utf-8")
    files = _synthetic(tmp_path)
    assert "stability_probe_total" not in produced_map(files, collect_definitions(files)), (
        "删掉唯一调用点后仍判为有生产者 → 判据太松，本守卫会假绿"
    )


def test_direct_and_aliased_writes_are_recognized(tmp_path):
    """四种真实写入形态必须都被认出（漏一种就是一条静默死规则）。"""
    (tmp_path / "m1.py").write_text(
        "from prometheus_client import Counter, Gauge, Histogram\n"
        "a_total = Counter('stability_direct_total', 'x')\n"
        "b = Gauge('stability_alias', 'x', ['q'])\n"
        "c_total = Counter('stability_module_attr_total', 'x')\n"
        "d = Histogram('stability_hist', 'x')\n",
        encoding="utf-8",
    )
    (tmp_path / "m2.py").write_text(
        "import m1\n"
        "from m1 import a_total\n"
        "from m1 import b as b_gauge\n"
        "def f():\n"
        "    a_total.inc()\n"                      # 直接导入
        "    m1.c_total.inc()\n"                  # 模块属性访问
        "    b_gauge.labels(q='x').set(1)\n"      # 别名导入 + labels 链
        "    m1.d.observe(0.5)\n",                # 直方图 observe
        encoding="utf-8",
    )
    files = _synthetic(tmp_path)
    defs = collect_definitions(files)
    produced = set(produced_map(files, defs))
    assert produced == {
        "stability_direct_total",
        "stability_alias",
        "stability_module_attr_total",
        "stability_hist",
    }, f"写入形态识别不全：{sorted(produced)}"
    assert _base_name("stability_hist_bucket") == "stability_hist", "直方图后缀归一失效"
