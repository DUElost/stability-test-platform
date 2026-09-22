"""#3091：script-presence 域的**写端点**必须 `require_admin`（结构守卫）。

存在理由：`POST /api/v1/script-presence/refresh` 落地时误用了 `get_current_active_user`
（读端点的写法），于是它成了本域唯一「非管理员可触发」的写端点——除账本 upsert 外还会
触发 agent 侧 `verify_scripts` RPC，且前端对非管理员可见可点。审计在 #3091 里点出该形态，
并要求「无论哪种处置，都补一条断言本域所有非只读端点都出现在要求管理员角色」的结构守卫，
防止下一次新增端点再漏。

判据（AST，纯文本扫描、不 import backend、不需要数据库）：

- 扫 `backend/api/routes/script_presence.py` 的 `@router.<method>(…)` 装饰器；
- `method` 不是 `get` 的端点函数，其默认参数里必须出现 `Depends(require_admin)`；
- 覆盖面自证：本域必须至少有 1 个读端点与 1 个写端点（否则判据可能因扫描失效而恒绿）。

豁免：无。若将来确有「非管理员可触发」的写端点，必须在本文件里登记**具体理由**并写明
失效条件（本守卫的豁免表当前为空 ⇒ 新增写端点默认被判红，这正是意图）。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES = REPO_ROOT / "backend/api/routes/script_presence.py"

READ_METHODS = {"get", "head", "options"}


def _route_endpoints() -> list[tuple[str, str, ast.FunctionDef]]:
    """``[(method, func_name, node)]``——本域全部路由端点（方法名小写）。"""
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    out: list[tuple[str, str, ast.FunctionDef]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                continue
            if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
                continue
            out.append((dec.func.attr.lower(), node.name, node))
    return out


def _depends_on_require_admin(node: ast.FunctionDef) -> bool:
    """默认参数里是否有 ``Depends(require_admin)``（`require_admin` 允许带括号调用）。"""
    for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
        if not isinstance(default, ast.Call):
            continue
        func = default.func
        if not (isinstance(func, ast.Name) and func.id == "Depends" and default.args):
            continue
        target = default.args[0]
        if isinstance(target, ast.Name) and target.id == "require_admin":
            return True
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Name) \
                and target.func.id == "require_admin":
            return True
    return False


def test_every_write_endpoint_requires_admin():
    endpoints = _route_endpoints()
    writes = [(m, n, node) for m, n, node in endpoints if m not in READ_METHODS]
    offenders = [name for _m, name, node in writes if not _depends_on_require_admin(node)]
    assert not offenders, (
        "script-presence 域的写端点必须 Depends(require_admin)（#3091）："
        f"{offenders}——若确为有意，请在本文件登记豁免并写明理由与失效条件"
    )


def test_scan_is_not_hollow():
    """覆盖面自证：本域读/写端点都得扫到（否则上面的判据可能是恒真的空扫）。"""
    methods = {m for m, _n, _node in _route_endpoints()}
    assert methods & READ_METHODS, "没扫到任何读端点——AST 判据可能已与实现脱节"
    assert methods - READ_METHODS, "没扫到任何写端点——本域的写面搬家了？同步本守卫"
