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
3. 反向也要成立：登记项若在扫描结果里消失（端点被删/改动词），同样报错——防登记表腐烂。

新增一个 admin-only GET 而不登记 → 本文件红：先回答「普通用户能不能走到它」，再决定是
加 `enabled: isAdmin`、页内收窄、AdminRoute 门控，还是把入口也藏起来（#2360 的两种做法）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROUTES_DIR = REPO_ROOT / "backend" / "api" / "routes"

_ROUTE_RE = re.compile(r'@router\.(get|post|put|patch|delete)\(\s*(?:f?["\'])(.*?)["\']')
_ANY_ROUTE_RE = re.compile(r"@router\.")

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


def admin_only_get_endpoints(routes_dir: Path) -> dict[str, int]:
    """扫出「GET + `Depends(require_admin)`」的端点 → 键为 ``<文件>:<路径>``。

    逐行状态机：记住最近一个 ``@router.<verb>`` 装饰器，遇到函数签名里的
    ``Depends(require_admin)`` 即判定该端点；遇到下一个装饰器则清空（避免把注释或
    别处的 ``require_admin`` 算进来）。
    """
    found: dict[str, int] = {}
    for path in sorted(routes_dir.glob("*.py")):
        pending: tuple[str, str] | None = None
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = _ROUTE_RE.match(line)
            if match:
                pending = (match.group(1).upper(), match.group(2))
                continue
            if _ANY_ROUTE_RE.match(line):
                pending = None
                continue
            if pending is not None and "Depends(require_admin)" in line:
                verb, route_path = pending
                if verb == "GET":
                    found[f"{path.name}:{route_path or '/'}"] = lineno
                pending = None
    return found


def test_scan_is_discriminative(tmp_path):
    """自证扫描器有判别力：新造的 admin-only GET 必须被抓到，写操作不抓。"""
    sample = tmp_path / "sample_routes.py"
    sample.write_text(
        """
@router.get("/open")
def open_one():
    return None


@router.get("/admin-read")
def admin_read(_user: User = Depends(require_admin)):
    return None


@router.post("/admin-write")
def admin_write(_user: User = Depends(require_admin)):
    return None
""",
        encoding="utf-8",
    )

    found = admin_only_get_endpoints(tmp_path)

    assert set(found) == {"sample_routes.py:/admin-read"}, (
        "扫描器口径不对：应只命中 admin-only 的 GET"
    )


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
