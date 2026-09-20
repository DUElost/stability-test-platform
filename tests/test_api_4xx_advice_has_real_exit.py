"""#2836：4xx `detail` 里承诺的出路必须真实存在（一类缺陷的静态守卫）。

**起因**：`DELETE /api/v1/plans/{id}` 在有 run 历史时返回
`cannot delete plan with N execution record(s); remove or archive plan runs first`——
而 `remove ... plan runs` 与 `archive` 两条出路**在本仓都不存在**（没有删 plan_run 的端点；
`POST /plan-runs/{id}/archive` 归档的是**日志**，守卫数的是 `PlanRun` 行数，
且 `plan_run` 表没有 `archived` 列）。于是「看起来可自助、实际只能找人」——
与 #2629（恒 0 死选项）、#2707（标题与首屏指向不同实体）同族：**文案承诺与现实不一致**。

本文件不是一次性改文案，而是把这一类钉住：

- **登记制**：扫全部 `backend/api/routes/*.py` 的 4xx `HTTPException(detail=…)`，
  命中「出路提示」的必须落在 `VERIFIED`（提示真指向存在的端点）或 `DEBT`
  （已知措辞与现实不符）里；没登记 ⇒ 红。
- **VERIFIED 是双向的**：登记的端点必须**在路由表里存在**，且 detail 里必须真的**点名**
  它（`name_cue`）——否则就是「登记了但没告诉用户」，与本单起因同形。
- **DEBT 只许缩短**：清单里的条目若已不在扫描结果中 ⇒ 红（措辞已修，请把条目删掉或
  升进 VERIFIED）；VERIFIED 与 DEBT 不得同时收同一条。
- **路由表抽取自证**：既断言已知端点抽得到，也断言**不存在的** `DELETE /api/v1/plan-runs/{id}`
  抽不到——否则本守卫会把假出路看成真的（那比没有守卫更糟）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES = REPO_ROOT / "backend" / "api" / "routes"

#: 「提示用户去做某事」的语言标记。宁窄勿宽：把陈述句（"Host has no IP configured"）
#: 也算进来会让登记表淹在噪声里，登记表一淹就没人再认真核对每一行。
OUTLET_CUES = re.compile(
    r"(\bfirst\b|请先|再删除|再试|\binstead\b|to modify|unarchive|use POST|via PUT"
    r"|请使用|可停用|\babort\b|\bretire\b|create a new|via run retention)",
    re.I,
)

#: 已核实：detail 点名的操作在路由表里存在（value = (端点, detail 里必须出现的点名词))
VERIFIED: dict[str, tuple[str, str]] = {
    "dedup.py|no scan result available, run scan first":
        ("POST /api/v1/plan-runs/{run_id}/dedup/scan", "scan"),
    "dedup.py|no merge result available, run merge first":
        ("POST /api/v1/plan-runs/{run_id}/dedup/merge", "merge"),
    "devices.py|archived project is read-only; unarchive to modify":
        ("POST /api/v1/projects/{project_key}/unarchive", "unarchive"),
    "hosts.py|主机有 {…} 个活跃 Job，请先 abort 再删除":
        ("POST /api/v1/plan-runs/{run_id}/abort", "abort"),
    "hosts.py|Host has no SSH credentials configured and is not found in A":
        ("PUT /api/v1/hosts/{host_id}", "PUT"),
    "plans.py|plan has {…} execution record(s); they are kept as history, ":
        ("PUT /api/v1/plans/{plan_id}", "PUT /api/v1/plans"),
    "scripts.py|contract fields cannot be changed on an existing version ({…":
        ("POST /api/v1/scripts/scan", "POST /scripts/scan"),
    "scripts.py|default_params cannot be changed on an existing version; cre":
        ("POST /api/v1/scripts/{name}/versions", "new script version"),
    "users.py|用户有 {…} 条审计记录，不可硬删除；如需禁止登录请使用停用（toggle-active）":
        ("POST /api/v1/users/{user_id}/toggle-active", "toggle-active"),
    "users.py|用户仍被其它记录引用，不可硬删除（可停用）":
        ("POST /api/v1/users/{user_id}/toggle-active", "停用"),
}

#: 已知债：措辞承诺的出路在路由表里不存在（或指向的不是那件事）。**只许缩短**。
#: 清偿方式二选一：把措辞改成真实出路并升进 VERIFIED，或真的补端点。
DEBT: dict[str, str] = {
    "hosts.py|主机当前 ONLINE，请先停止 Agent 服务再删除":
        "出路只在系统层（Agent systemd），API 面没有「停止 Agent」端点；措辞应改指向退役流程",
    "hosts.py|主机有 {…} 条历史 Job 记录，删除会清空执行历史；请先归档/清理后再删除":
        "真实出路是 POST /api/v1/hosts/{host_id}/retire（退役），但措辞写「归档/清理」——"
        "动词指向不存在的操作，与 #2836 起因同形",
    "hosts.py|主机下仍有 {…} 台设备，请先移除或迁移设备":
        "设备侧没有删除/改挂 host 的端点（只有 PUT tags 与 POST bulk-project 改项目）",
    "hosts.py|主机有 {…} 条执行投影记录，请先清理关联 Run 后再删除":
        "没有清 PlanRunHost 的端点；run 由 retention 按龄老化，措辞暗示可手动清",
    "hosts.py|主机仍被其它记录引用，不可硬删除（请先清理关联数据）":
        "「关联数据」无对应端点，操作者无法自助；应给出可核对的引用清单",
}


# ── 抽取 ────────────────────────────────────────────────────────────────────

def _normalized_detail(node: ast.Call) -> str | None:
    """取 `detail=` 的字符串；f-string 的动态段统一成 `{…}`。"""
    for kw in node.keywords:
        if kw.arg != "detail":
            continue
        if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
        if isinstance(kw.value, ast.JoinedStr):
            return "".join(
                v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{…}"
                for v in kw.value.values
            )
    return None


def _outlet_hints() -> dict[str, str]:
    """`{登记键: detail 原文}`——扫全部路由文件里的 4xx 出路提示。"""
    found: dict[str, str] = {}
    files = sorted(ROUTES.glob("*.py"))
    assert len(files) > 15, f"路由目录扫描面塌陷：只看到 {len(files)} 个文件"
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException"):
                continue
            code = next(
                (k.value.value for k in node.keywords
                 if k.arg == "status_code" and isinstance(k.value, ast.Constant)),
                None,
            )
            detail = _normalized_detail(node)
            if not (isinstance(code, int) and 400 <= code < 500 and detail):
                continue
            if not OUTLET_CUES.search(detail):
                continue
            found[f"{path.name}|{detail[:60]}"] = detail
    assert found, "一条出路提示都没扫到——正则或路由形态已变，本守卫会静默失去覆盖"
    return found


def _route_table() -> set[str]:
    """`{"METHOD /prefix/path", …}`：从 routes 文件的 APIRouter(prefix=…) 与装饰器抽。"""
    routes: set[str] = set()
    for path in sorted(ROUTES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if getattr(node.value.func, "id", "") != "APIRouter":
                continue
            prefix = next(
                (k.value.value for k in node.value.keywords
                 if k.arg == "prefix" and isinstance(k.value, ast.Constant)),
                "",
            )
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                routes |= _from_decorator(deco, prefixes)
    return routes


def _from_decorator(deco: ast.expr, prefixes: dict[str, str]) -> set[str]:
    if not (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)):
        return set()
    method = deco.func.attr.lower()
    if method not in {"get", "post", "put", "patch", "delete"}:
        return set()
    router = getattr(deco.func.value, "id", "")
    if router not in prefixes or not deco.args:
        return set()
    first = deco.args[0]
    if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
        return set()
    return {f"{method.upper()} {prefixes[router]}{first.value}"}


# ── 判据 ────────────────────────────────────────────────────────────────────


def test_every_outlet_hint_is_registered() -> None:
    hints = _outlet_hints()
    unknown = sorted(set(hints) - set(VERIFIED) - set(DEBT))
    assert not unknown, (
        "这些 4xx 提示承诺了操作，却没登记它是真的出路还是已知债。"
        "出路确实存在的升进 VERIFIED（填端点与点名词），不存在的记进 DEBT：\n"
        + "\n".join(f"  {k}" for k in unknown)
    )


def test_verified_hints_point_at_existing_named_routes() -> None:
    routes = _route_table()
    hints = _outlet_hints()
    problems: list[str] = []
    for key, (route, cue) in VERIFIED.items():
        if route not in routes:
            problems.append(f"{key}: 登记的出路 {route} 不在路由表里")
            continue
        detail = hints.get(key)
        if detail is None:
            problems.append(f"{key}: 已不在扫描结果中——措辞改过，请把条目删掉或同步 key")
            continue
        if cue.lower() not in detail.lower():
            problems.append(
                f"{key}: detail 没点名 {route}（缺关键词 {cue!r}）"
                "——出路存在但用户不知道，正是 #2836 的形状"
            )
    assert not problems, "\n".join(problems)


def test_debt_list_is_live_and_shrink_only() -> None:
    hints = _outlet_hints()
    stale = sorted(set(DEBT) - set(hints))
    assert not stale, f"这些债务条目已不在扫描结果中（措辞已修），请从 DEBT 删除：{stale}"
    drained = sorted(k for k, v in VERIFIED.items() if k in DEBT)
    assert not drained, f"同一提示既 VERIFIED 又 DEBT：{drained}"
    # DEBT 不是垃圾桶：每条必须写清「真实出路是什么」，否则下一个读者只能重新查一遍。
    for key, note in DEBT.items():
        assert len(note) >= 25, f"{key}: 债务说明太短，无法判断怎么清偿"
        assert key in hints, f"{key}: 扫描结果里没有它，却仍挂在 DEBT 上"


def test_route_table_extraction_self_proof() -> None:
    """抽取器必须既认得真的、也**不**认得假的——否则本守卫会替假出路背书。"""
    routes = _route_table()
    for known in (
        "POST /api/v1/plan-runs/{run_id}/abort",       # hosts.py 的 abort 出路靠它成立
        "POST /api/v1/plan-runs/{run_id}/dedup/scan",  # 双 router（prefix 不同）必须都被抽到
        "PUT /api/v1/plans/{plan_id}",
        "DELETE /api/v1/plans/{plan_id}",
        "POST /api/v1/users/{user_id}/toggle-active",
        "POST /api/v1/scripts/scan",
        "POST /api/v1/projects/{project_key}/unarchive",
    ):
        assert known in routes, f"抽取漏了真实端点：{known}"
    # 本单的起因：这两条都不存在。若哪天抽取器宽到能把它们认成真的，
    # 「出路是否存在」的判定就退化成自我确认。
    for fake in (
        "DELETE /api/v1/plan-runs/{run_id}",
        "POST /api/v1/plan-runs/{run_id}/archive-run",
        "DELETE /api/v1/hosts/{host_id}/jobs",
    ):
        assert fake not in routes, f"抽取器把不存在的端点当成了真的：{fake}"


def test_the_original_lie_is_gone() -> None:
    """起因文案本身要有钉子：它一旦被改回 detail，这里必须立刻红。

    只判 `detail=` 的字符串（走 AST），**不判注释里的字**——注释要能继续解释这段历史，
    否则等于逼着后来人删掉说明（#2641/#2642 的教训：判据落在文字上就会被文字骗）。
    """
    details: list[str] = []
    for path in sorted(ROUTES.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "HTTPException":
                detail = _normalized_detail(node)
                if detail:
                    details.append(detail)
    assert details, "一条 4xx detail 都没解析到——判据会静默失去覆盖"
    assert not [d for d in details if "remove or archive plan runs first" in d], (
        "#2836 的假出路文案回来了（plan_run 既无删除端点、archive 也只归档日志）"
    )
