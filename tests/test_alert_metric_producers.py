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
2. **写入点** = mutator 调用链里出现该标识符，覆盖四种真实形态：
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
5. **框架回调这一跳 AST 看不见**，另钉 `_MANUAL_WIRED_METRICS`：写入点位于中间件
   `dispatch` 里的指标（`stability_api_requests_total` ← `ApiRequestMetricsMiddleware`），
   必须能在 `backend/main.py` 的 AST 里找到 `add_middleware(ApiRequestMetricsMiddleware)`
   调用。用 AST 而非子串是实测教训：子串匹配会被「把整行注释掉」骗过，D 组对照最初
   就是绿的。
6. **容器间接写入**另钉 `_CONTAINER_WIRED_METRICS`（#2286）：指标对象进元组、由
   `for ..., gauge, ... in _FLEET_GAUGES: gauge.labels(...).set(...)` 写入
   （`stability_host_online` / `stability_device_online`）。AST 里「指标标识符」和
   「写入调用」不在同一条链上，强判据看不见；锚点要求「容器赋值 + 遍历该容器的循环 +
   循环变量上的 mutator」三段同时成立，任一段被改写就退回「无生产者」。

判据边界（不是全量可达性证明）：只追**一跳**，且一跳的终点是「代码里存在调用点」。
「调用点本身是否会在运行时被走到」（分支永假、任务未注册、路由未挂载）不由本文件判定——
那要执行期证据。本文件消灭的是最省事的一类：定义了、门面也写了、但**没有任何人调门面**。

存量：#2151 已逐条核对 17 条告警引用的指标都有真实生产者，故允许清单为空
（新门禁落地不背存量，同 S14 口径）。将来确实无生产者却仍挂告警 → 必须进
`_ALERT_UNPRODUCED_ALLOWLIST` 且带原因与终态出口，不许无解释豁免。

消费方不止告警面：判据是共享分析器，`tests/test_grafana_dashboard_contract.py` 的
「面板不得引用无生产者指标」自 #2286 起从 `unproduced_definitions()` **派生**——原来是
手维护的 `UNPRODUCED_METRICS` 清单，生产者一落地就过期成恒真豁免（同 #1258）。把判据从
「被引用面」扩到**全指标面**（每个定义的指标都必须有生产者）由 #2287 负责，前置是清掉
当前 10 条真无生产者的存量。

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
# prometheus_client 的写入方法（labels() 返回子序列，视为写入意图）。
# 名单必须覆盖**全部已声明指标类型**：#2286 核对存量时发现只列了 Counter/Gauge/
# Histogram/Summary 的方法，漏掉 Info 的 `.info()` —— 于是 `stability_build`
# 被判成死指标（真写入在 `backend/core/metrics.py:968` 的 `init_build_info`，由
# `backend/main.py:176` 调用），而 Build Version 面板其实有数据。漏一个方法 = 一类指标全体假红。
_MUTATORS = {
    "inc", "dec", "observe", "set", "labels", "remove", "delete",  # Counter/Gauge/Histogram/Summary
    "info",           # Info（Build Version 面板就靠它）
    "state",          # Enum
    "exceptions",     # Counter.exceptions()
    "time", "set_to_current_time",  # Gauge.time()/set_to_current_time()
}
_HISTOGRAM_SUFFIXES = ("_bucket", "_count", "_sum", "_created")
# `Info` 导出的样本名带 `_info` 后缀（prometheus_client `Info._child_samples`），
# 代码里声明的是 `stability_build`、面板查询的是 `stability_build_info`。`Enum` 是
# stateset、用原名，不需要后缀。少这一条，Info 指标在消费侧永远解析不到定义。
_INFO_SUFFIXES = ("_info",)

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
    for suffix in _HISTOGRAM_SUFFIXES + _INFO_SUFFIXES:
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


def _unproduced_from(files, defs: dict[str, dict]) -> dict[str, str]:
    """全指标面「有定义、查无生产者证据」→ {基础指标名: 原因}。

    证据 = 跨文件直写 / 定义文件内 helper 一跳 / `_MANUAL_WIRED_METRICS` /
    `_CONTAINER_WIRED_METRICS`。四类都不成立才记为无生产者。
    """
    produced = produced_map(files, defs)
    wired = container_wired_map(files, defs)
    weak = weak_reference_sites(files, defs)
    problems: dict[str, str] = {}
    for base, definition in defs.items():
        if base in produced or base in wired or base in _MANUAL_WIRED_METRICS:
            continue
        hints = ", ".join(weak.get(base, [])[:3])
        where = f"{definition['file']}:{definition['line']}"
        if hints:
            problems[base] = (
                f"{where} 无直接写入点，但被跨文件引用（{hints}）——疑似经容器/变量间接持有；"
                f"确认接线后进 _CONTAINER_WIRED_METRICS 或 _MANUAL_WIRED_METRICS 登记证据，"
                f"别放宽本判据"
            )
        else:
            problems[base] = f"{where} 有定义、无任何写入点也无跨文件引用"
    return problems


def unproduced_definitions() -> dict[str, str]:
    """对全仓非测试源码跑一次判据（仪表板面与 #2287 的全指标面棘轮共用入口）。"""
    files = _iter_source_files(_SCAN_ROOT)
    return _unproduced_from(files, collect_definitions(files))


def unproduced_alert_metrics() -> dict[str, str]:
    """返回 {告警引用的指标名: 失败原因}，只统计**告警引用到**的指标。"""
    files = _iter_source_files(_SCAN_ROOT)
    defs = collect_definitions(files)
    unproduced = _unproduced_from(files, defs)
    problems: dict[str, str] = {}
    for metric in alert_metric_names():
        base = _resolve(metric, defs)
        if base is None:
            problems[metric] = "非测试代码里找不到该指标的 Counter/Gauge/... 定义"
        elif base in unproduced:
            problems[metric] = unproduced[base]
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
# 分两张表，因为「证据长什么样」不同：
#   `_MANUAL_WIRED_METRICS`  框架回调（中间件 `dispatch` 由 ASGI 调）
#       每条 = 指标名 -> (文件, 调用名, 作为实参出现的对象名)；
#   `_CONTAINER_WIRED_METRICS` 指标对象塞进容器再遍历写入（`_FLEET_GAUGES`）
#       每条 = 指标名 -> (文件, 容器赋值名)。
# 用 AST 而不是子串：实测子串匹配会被「把整行注释掉」骗过（D 组对照最初就是绿的）。
_MANUAL_WIRED_METRICS: dict[str, tuple[str, str, str]] = {
    "stability_api_requests_total": (
        "backend/main.py",
        "add_middleware",
        "ApiRequestMetricsMiddleware",
    ),
}


# AST 追不到的第二类接线：指标对象进容器、由遍历写入（#2237 的 Note 把「扩展锚点
# 形态」列为这类指标转正的唯一出口，#2286 落地）。每条 = 指标名 -> (所在文件, 容器赋值名)。
_CONTAINER_WIRED_METRICS: dict[str, tuple[str, str]] = {
    "stability_host_online": ("backend/api/routes/metrics.py", "_FLEET_GAUGES"),
    "stability_device_online": ("backend/api/routes/metrics.py", "_FLEET_GAUGES"),
}


def _container_wiring(tree: ast.Module, container: str, ident: str) -> list[tuple[int, str]]:
    """``ident`` 是否真的经模块级容器 ``container`` 被写入；返回证据（空 = 接线不成立）。

    三段缺一即退回「无生产者」，因此放宽到容器不会放行「塞进元组却从不写入」：

    1. 模块级 ``container = (...)`` 的值里出现 ``ident``；
    2. 存在 ``for <target> in container``；
    3. ``<target>``（解包元组）里有一个名字，在该循环体内以 ``<name>.<mutator>(...)`` 被调用。
    """
    holder: ast.expr | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == container for target in node.targets
        ):
            holder = node.value
    if holder is None:
        return []
    if not any(isinstance(n, ast.Name) and n.id == ident for n in ast.walk(holder)):
        return []
    evidence: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor)):
            continue
        if not isinstance(node.iter, ast.Name) or node.iter.id != container:
            continue
        if not isinstance(node.target, ast.Tuple):
            continue
        bound = {el.id for el in node.target.elts if isinstance(el, ast.Name)}
        lo = node.lineno
        hi = node.end_lineno or node.lineno
        for chain, line, parts in _mutator_calls(tree):
            if lo <= line <= hi and parts[0] in bound:
                evidence.append((node.lineno, f"for {parts[0]} in {container}: {chain}()"))
    return evidence


def container_wired_map(files, defs: dict[str, dict]) -> dict[str, list[str]]:
    """`_CONTAINER_WIRED_METRICS` 中**当前仍然成立**的接线 → {基础指标名: 证据}。"""
    trees = {rel: tree for rel, _path, tree in files}
    out: dict[str, list[str]] = {}
    for metric, (rel, container) in _CONTAINER_WIRED_METRICS.items():
        base = _resolve(metric, defs)
        tree = trees.get(rel)
        if base is None or tree is None:
            continue
        evidence = _container_wiring(tree, container, defs[base]["ident"])
        if evidence:
            out[base] = [f"{rel}:{line} {text}" for line, text in evidence]
    return out


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


def test_wiring_registries_stay_wired():
    """两张人工登记表钉住的接线仍在场（且清单不长成噪声）。

    登记表的用途不对称，判据也不同：

    - `_MANUAL_WIRED_METRICS`（框架回调）：`record_api_request` 的调用点虽然能被
      helper 一跳看到，但那一跳的终点在 `ApiRequestMetricsMiddleware.dispatch` 里，
      **谁调 dispatch 由 ASGI 决定**。所以「中间件有没有真挂载」只有本锚点能证明，
      指标已被强判据认出来也不构成删除理由（否则 #2237 的 D 组对照又会变绿）。
      代价是清单必须仍然被告警引用，否则就是噪声。
    - `_CONTAINER_WIRED_METRICS`（容器间接写入）：锚点本身是生产者证据，一旦强判据
      能直接认出写入点，登记表就该删掉，故额外要求「仍是无生产者的那一类」。
    """
    referenced = set(alert_metric_names()) | {_base_name(m) for m in alert_metric_names()}
    problems: list[str] = []
    files = _iter_source_files(_SCAN_ROOT)
    defs = collect_definitions(files)
    produced = produced_map(files, defs)
    wired = container_wired_map(files, defs)
    for metric, (rel, container) in sorted(_CONTAINER_WIRED_METRICS.items()):
        base = _resolve(metric, defs)
        if base is None:
            problems.append(f"{metric}: 注册表里已无该定义，请从 _CONTAINER_WIRED_METRICS 删除")
            continue
        if base in produced:
            problems.append(f"{metric}: 强判据已能直接认出写入点，容器锚点已多余，请删除")
            continue
        if base not in wired:
            problems.append(
                f"{metric}: {rel} 里已找不到「{container} 赋值 + 遍历该容器 + 循环变量上 "
                f"mutator」的完整接线（容器被改名/循环被改写 → 指标已无生产者）"
            )
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


def test_info_metric_written_by_same_file_helper_is_produced(tmp_path):
    """`Info` 指标经 `.info()` 写入的形态必须被认出（#2286 实测漏判的回归用例）。

    这就是 `init_build_info` 的形状：写入在定义文件的 helper 里、用的既不是 inc 也不
    是 set，跨文件只有一个 `init_build_info(...)` 调用。mutator 名单少一个方法，这条
    链整条断开，面板有数据的指标会被判成死规则。
    """
    (tmp_path / "core_m.py").write_text(
        "from prometheus_client import Info\n"
        "build_info = Info('stability_probe_build', 'b')\n"
        "def init_probe_build_info():\n"
        "    build_info.info({'version': '1'})\n",
        encoding="utf-8",
    )
    (tmp_path / "boot.py").write_text(
        "from core_m import init_probe_build_info\n"
        "def lifespan():\n"
        "    init_probe_build_info()\n",
        encoding="utf-8",
    )
    files = _synthetic(tmp_path)
    defs = collect_definitions(files)
    produced = produced_map(files, defs)
    assert "stability_probe_build" in produced, (
        "`.info()` 写入未被认出 → mutator 名单又漏了方法，全量核对存量数字也不可信"
    )


def test_container_wiring_anchor_is_discriminative():
    """容器锚点必须自己站得住：三段证据任缺一段就退回「无生产者」（#2286）。

    复刻 `backend/api/routes/metrics.py` 的 `_FLEET_GAUGES` 形状。它服务的是**仪表板面**
    ——面板引用 `stability_host_online` / `stability_device_online`，判据看不见生产者时
    只能退回人工豁免，而手维护豁免正是 #1258 过期的形态。
    """
    src = (
        "host_online = Gauge('stability_probe_host_online', 'h')\n"
        "device_online = Gauge('stability_probe_device_online', 'd')\n"
        "_PROBE_GAUGES = ((Host, host_online), (Device, device_online))\n"
        "def _refresh(db):\n"
        "    for model, gauge in _PROBE_GAUGES:\n"
        "        gauge.labels(status='up').set(1)\n"
    )
    tree = ast.parse(src)
    assert _container_wiring(tree, "_PROBE_GAUGES", "host_online"), "容器间接写入未被认出"
    # 循环体不再写循环变量（改写成别的对象）→ 证据必须消失
    assert not _container_wiring(
        ast.parse(src.replace("gauge.labels(status='up').set(1)", "model.foo = 1")),
        "_PROBE_GAUGES",
        "host_online",
    ), "循环里已无写入仍算生产者 → 容器判据过松"
    # 容器里不再有该指标 → 证据必须消失
    assert not _container_wiring(
        ast.parse(src.replace("(Host, host_online)", "(Host, other_gauge)")),
        "_PROBE_GAUGES",
        "host_online",
    ), "指标已不在容器里仍算生产者 → 容器判据过松"
    # 只有赋值、没有遍历（`_FLEET_GAUGES` 变成死数据）→ 证据必须消失
    assert not _container_wiring(
        ast.parse(src.split("def _refresh")[0]),
        "_PROBE_GAUGES",
        "host_online",
    ), "容器无人遍历仍算生产者 → 放行死指标"


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
