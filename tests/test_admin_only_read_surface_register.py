"""#2360/#2418 族守卫：admin-only **读**接口必须登记「前端可达性」依据。

**缺陷类**：页面入口对普通用户可见、其接口却是 `require_admin` → 「点进去满页 403」。
已发生两例：**#2360**（WiFi 资源池入口可见但列表/详情/loads 全 admin-only，本单已修）、
**#2418**（报告页状态徽标恒「未知」——同类「契约与呈现没对齐」的另一形态）。

2026-09-17 做过一次全量清点（81 处 `require_admin` → 按动词分层，写操作被拦是**正当**的，
只看 **GET**）并逐条核对前端可达性：13 个 admin-only 读接口，除已修的 #2360 外，其余都在
AdminRoute 页面下 / 页内按角色收窄 / 前端零调用方。本文件把这次结论固化成**棘轮**：

1. 扫 `backend/api/routes/*.py` 里「`@router.get` + `Depends(require_admin)`」的端点，
   每个必须在 `_REGISTERED` 里（键 = `<文件>:<路径或 '/' >`）；
2. 登记项必须写明**为什么非 admin 走到这个页面不会吃 403**（入口隐藏 / AdminRoute /
   页内收窄 / 前端不调，四种之一）；
3. 反向也要成立：登记项若在扫描结果里消失（端点被删/改动词），同样报错——防登记表腐烂；
4. **扫描面完整性**（#2642 补）：源码里每一个 `@router.<verb>(` 都必须被解析成一个端点。
   原扫描器是**逐行状态机**，「路径写在下一行」的装饰器会被整条丢弃——那种端点既不被要求登记、
   也不出现在结果里，于是第 3 条的反向检查同样拦不住（它只看「已登记的是否还在」）。
   本仓 `@router.get(` 换行写法当时已有 12 处（`logs.py` 1 / `plan_runs.py` 6 / `projects.py` 3 / `runs.py` 1）。

新增一个 admin-only GET 而不登记 → 本文件红：先回答「普通用户能不能走到它」，再决定是
加 `enabled: isAdmin`、页内收窄、AdminRoute 门控，还是把入口也藏起来（#2360 的两种做法）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = REPO_ROOT / "backend" / "api" / "routes"

#: 装饰器起点。`\s*` 覆盖「路径换行」写法（`\s` 含换行，故不依赖 re.S）；
#: 引号用反向引用配对，避免 `"...'` 这类错配。
_ROUTE_RE = re.compile(
    r"""@router\.(get|post|put|patch|delete)\s*\(\s*f?(["'])(.*?)(?<!\\)\2"""
)
#: 端点的依赖声明只到下一个函数签名为止（再往后的 `require_admin` 属于别的函数）
_NEXT_DEF_RE = re.compile(r"^(?:async\s+)?def\s", re.M)
_ADMIN_DEP_RE = re.compile(r"Depends\(\s*require_admin\s*\)")

#: 端点 → 该端点为什么对普通用户「不可见 / 不会 403」。
#: 四种依据：AdminRoute（页面在 AdminRoute 下）/ 入口隐藏（导航 adminOnly）/
#: 页内收窄（同一页按角色分权）/ 前端不调（无调用方，仅诊断面）。
_REGISTERED: dict[str, str] = {
    "ai_assistant.py:/config": (
        "AssistantPage 的 config 查询 enabled: isAdmin（普通用户不调 admin 端点）；"
        "配置页 AiAssistantSettingsPage 在 AdminRoute 下"
    ),
    "ai_assistant.py:/actions/pending": (
        "AssistantApprovalsPage（/assistant/approvals）在 AdminRoute 下"
    ),
    "audit.py:/": "AuditLogPage（/audit）在 AdminRoute 下",
    "hosts.py:/{host_id}/log-signal-dead-letters": (
        "前端零调用方（主机页诊断端点，仅管理侧/脚本使用）"
    ),
    # #2642：这条此前**不在扫描结果里**——它的路径写在下一行，逐行状态机把整条装饰器丢了。
    # 换 ast 解析后才被看见；结论是良性的（前端只有类型镜像、无调用方），但**盲区本身**
    # 才是本单要修的：下一个用同样写法的新端点不会自带这份结论。
    "logs.py:/log-signals/orphans": (
        "前端零调用方（types.ts 只有类型镜像，无 fetch/hook；#213 D3 的孤儿 signal 诊断端点）"
    ),
    "notifications.py:/channels": "通知页内按角色收窄：配置页签（渠道）仅 admin 可见",
    "notifications.py:/rules": "通知页内按角色收窄：配置页签（规则）仅 admin 可见",
    "resource_pools.py:/": "#2360：入口 adminOnly 隐藏 + /wifi 路由移入 AdminRoute",
    "resource_pools.py:/loads": "#2360：同 WiFi 资源池页（入口隐藏 + 路由门控）",
    "resource_pools.py:/{pool_id}": "#2360：同 WiFi 资源池页（入口隐藏 + 路由门控）",
    "settings.py:/": "SettingsPage（/settings）在 AdminRoute 下",
    "stats.py:/file-server": "FileServerPage（/storage）在 AdminRoute 下",
    "users.py:/": "UsersPage（/users）在 AdminRoute 下 + 导航 adminOnly",
    "users.py:/{user_id}": "UsersPage（/users）在 AdminRoute 下 + 导航 adminOnly",
}


#: `@router.<verb>(` 的装饰器形态（`router.get(...)` / `router.delete(...)` 等）
_ROUTER_DECORATOR_RE = re.compile(r"@router\.(get|post|put|patch|delete)\s*\(")


def _is_depends_on(node: ast.AST, name: str) -> bool:
    """`Depends(require_admin)`——也认 `fastapi.Depends(...)` 与 `... .require_admin`。"""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    dep_ok = (isinstance(func, ast.Name) and func.id == "Depends") or (
        isinstance(func, ast.Attribute) and func.attr == "Depends")
    if not (dep_ok and node.args):
        return False
    target = node.args[0]
    return (isinstance(target, ast.Name) and target.id == name) or (
        isinstance(target, ast.Attribute) and target.attr == name)


def _endpoint_is_admin(fn: ast.AST, dec: ast.Call) -> bool:
    """端点的 admin 依赖只认**签名与装饰器**，不认函数体——避免把 body 里的同名调用算进来。

    覆盖三种写法：参数默认值 `= Depends(require_admin)`、`Annotated[..., Depends(...)]`、
    以及装饰器里的 `dependencies=[Depends(require_admin)]`。
    """
    args = fn.args
    haystacks: list[ast.AST] = [
        *dec.args, *dec.keywords,
        *args.posonlyargs, *args.args, *args.kwonlyargs,
        *[a for a in (args.vararg, args.kwarg) if a is not None],
        *args.defaults, *args.kw_defaults,
    ]
    for node in haystacks:
        if any(_is_depends_on(child, "require_admin") for child in ast.walk(node)):
            return True
    return False


def iter_route_endpoints(routes_dir: Path) -> list[tuple[str, str, int, bool, str]]:
    """解析路由目录里的每一个 `@router.<verb>` 端点（用 **ast**，不是逐行文本状态机）。

    返回 ``(文件名, 动词, 路径, 行号, 是否 admin-only)``。
    换行/参数换行/`Annotated` 都不影响——#2642 的盲区正是「路径写在下一行」时，
    逐行状态机把整条装饰器丢弃，那种端点既不被要求登记、也不出现在扫描结果里，
    连反向检查（登记项是否还在）都拦不住。
    """
    endpoints: list[tuple[str, str, int, bool, str]] = []
    for path in sorted(routes_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in [n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for dec in fn.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute):
                    continue
                if not isinstance(dec.func.value, ast.Name) or dec.func.value.id != "router":
                    continue
                verb = dec.func.attr
                if verb not in ("get", "post", "put", "patch", "delete"):
                    continue
                route_path = dec.args[0].value if (
                    dec.args and isinstance(dec.args[0], ast.Constant)
                    and isinstance(dec.args[0].value, str)) else ""
                endpoints.append(
                    (path.name, verb, dec.args[0].lineno if dec.args else dec.lineno,
                     _endpoint_is_admin(fn, dec), route_path)
                )
    return endpoints


def count_route_decorators(routes_dir: Path) -> int:
    """源码里 `@router.<verb>(` 的**字面出现次数**（独立于 ast 的第二种实现）。

    与 `iter_route_endpoints` 的数量对不上 ⇒ 两个解析器至少有一个瞎了。这是交叉核对，
    不是同一判据写两遍：ast 漏的是「装饰器不在函数上/形态奇特」，正则漏的是「文本写法」，
    #2639 要的就是让「用例过期」与「防线回归」可区分。
    """
    total = 0
    for path in sorted(routes_dir.glob("*.py")):
        total += len(_ROUTER_DECORATOR_RE.findall(path.read_text(encoding="utf-8")))
    return total


def admin_only_get_endpoints(routes_dir: Path) -> dict[str, int]:
    """扫出「GET + `Depends(require_admin)`」的端点 → 键为 ``<文件>:<路径>``。"""
    return {
        f"{name}:{route_path or '/'}": lineno
        for name, verb, lineno, is_admin, route_path in iter_route_endpoints(routes_dir)
        if is_admin and verb == "get"
    }


def test_scan_is_discriminative(tmp_path):
    """自证扫描器有判别力：各种写法都要判对（#2642 的盲区就在「路径换行」）。

    这里刻意把**同一语义**写成 5 种排版，逐条钉住结论——旧实现是逐行状态机，
    `@router.get(` 后换行即整条丢弃（既不被要求登记，也不出现在结果里）。
    """
    sample = tmp_path / "sample_routes.py"
    sample.write_text(
        '''
@router.get("/open")
def open_one():
    return None


@router.get("/admin-read")
def admin_read(_user: User = Depends(require_admin)):
    return None


@router.post("/admin-write")
def admin_write(_user: User = Depends(require_admin)):
    return None


@router.get(
    "/path-on-next-line",
)
def path_next_line(_user: User = Depends(require_admin)):
    return None


@router.get(
    "/multiline-args",
    response_model=ApiResponse[list[int]],
)
def multiline_args(
    skip: int = Query(0),
    _user: User = Depends(require_admin),
):
    return []


@router.get("/annotated-style")
def annotated_style(_user: Annotated[User, Depends(require_admin)]):
    return None


@router.get("/dependencies-kwarg", dependencies=[Depends(require_admin)])
def dependencies_kwarg():
    return None


@router.get("/admin-but-next-is-open")
def admin_then_body_reads(_user: User = Depends(require_admin)):
    # body 里出现同名调用不该被算成依赖（反向：也不该让下一个端点偷到上一个的 admin）
    return helper(Depends(require_admin))


@router.get("/last-endpoint-no-trailing-def")
def the_tail(_u: User = Depends(require_admin)):
    return None
''',
        encoding="utf-8",
    )

    found = admin_only_get_endpoints(tmp_path)

    assert set(found) == {
        "sample_routes.py:/admin-read",
        "sample_routes.py:/path-on-next-line",
        "sample_routes.py:/multiline-args",
        "sample_routes.py:/annotated-style",
        "sample_routes.py:/dependencies-kwarg",
        "sample_routes.py:/admin-but-next-is-open",
        "sample_routes.py:/last-endpoint-no-trailing-def",
    }, f"扫描器口径不对（应含 3 种换行写法，且不得把写操作/开放端点算进来）：{sorted(found)}"
    # 写操作与开放端点都不在：写操作被拦是正当的（#2418 口径），开放端点本就不需要登记
    assert "sample_routes.py:/open" not in found
    assert "sample_routes.py:/admin-write" not in found


def test_body_only_dependency_does_not_leak(tmp_path):
    """admin 判定只看**签名与装饰器**：函数体里的 `Depends(require_admin)` 不算数。"""
    sample = tmp_path / "body_only.py"
    sample.write_text(
        '''
@router.get("/body-only")
def body_only():
    other = SomeEndpoint(Depends(require_admin))
    return other
''',
        encoding="utf-8",
    )
    assert admin_only_get_endpoints(tmp_path) == {}, "body 里的同名调用被误判成 admin 依赖"


def test_scan_surface_is_not_empty():
    """防恒真：扫描面塌了（路由写法变化/目录改名）不能让本文件永远绿。"""
    found = admin_only_get_endpoints(ROUTES_DIR)
    assert len(found) >= 10, f"admin-only GET 扫描面塌了：{sorted(found)}"
    assert "resource_pools.py:/" in found


def test_every_admin_only_get_is_registered():
    """新增 admin-only GET 必须登记「普通用户为什么不会吃 403」。"""
    found = admin_only_get_endpoints(ROUTES_DIR)
    missing = sorted(set(found) - set(_REGISTERED))
    assert not missing, (
        "以下 admin-only 读接口没有登记前端可达性依据（#2360 族：入口可见 + 接口 403 "
        f"= 满页 403）：{missing}\n"
        "请先回答「普通用户能不能走到它」，再决定加 enabled: isAdmin / 页内收窄 / "
        "AdminRoute 门控 / 入口隐藏，然后登记到 _REGISTERED。"
    )


def test_registered_entries_still_exist():
    """反向：登记项在扫描结果里消失（端点删了/改了动词）→ 登记表该同步。"""
    found = admin_only_get_endpoints(ROUTES_DIR)
    stale = sorted(set(_REGISTERED) - set(found))
    assert not stale, f"登记表里有已不存在的端点（同步删掉）：{stale}"


def test_every_entry_states_a_reason():
    """依据必须是「四种之一」且写清楚——空话与占位符不算依据。"""
    for key, reason in _REGISTERED.items():
        assert len(reason) >= 12, f"{key} 的依据太短，写清楚是哪一种（入口隐藏/AdminRoute/页内收窄/前端不调）"
        assert any(
            marker in reason
            for marker in ("AdminRoute", "入口", "收窄", "零调用方", "隐藏")
        ), f"{key} 的依据没点明属于哪一种（入口隐藏/AdminRoute/页内收窄/前端不调）：{reason}"


def test_two_parsers_agree_on_the_real_tree():
    """两套**独立实现**必须数出同一批装饰器：ast 解析 vs 正则字面计数。

    这不是把同一判据写两遍——两者失明的方式不同：ast 看不见「装饰器不挂在函数上」
    （动态注册、`getattr(router, verb)` 之类），正则看不见语义（换行/嵌套写法它都数得到，
    但分不清 GET 与 admin）。#2642 的形态恰是「正则数得到、旧状态机解析不到」，
    有这条交叉核对，扫描面再萎缩就会在这里暴露，而不是静默少一个端点。
    """
    parsed = len(iter_route_endpoints(ROUTES_DIR))
    textual = count_route_decorators(ROUTES_DIR)
    assert parsed == textual, (
        f"路由端点解析数 {parsed} != 源码里 `@router.<verb>(` 出现数 {textual}"
        "——解析器漏了某种写法，扫描面正在静默萎缩（#2642）"
    )
    assert parsed >= 150, f"解析到的端点少得可疑（{parsed}），先确认 ROUTES_DIR 没指错"
