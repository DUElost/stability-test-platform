"""Static regression guards for unified frontend filter workbench components.

源扫描型否定断言必须经 SourceGuard 锚点先行（#2639）：否则被扫逻辑搬走后
`assert "<select" not in text` 会恒真，守卫仍绿却已空守。
"""

from __future__ import annotations

from pathlib import Path
import re

from tools.dev.source_anchor import SourceGuard

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

PROJECT_FILTER_SELECT = "frontend/src/components/project/ProjectFilterSelect.tsx"
DEVICE_FILTER_BAR = (
    "frontend/src/components/execution/plan-execute/DeviceFilterBar.tsx"
)
EXPANDABLE_DEVICE_TABLE = "frontend/src/components/device/ExpandableDeviceTable.tsx"
PLAN_EXECUTE_PAGE = "frontend/src/pages/execution/PlanExecutePage.tsx"


def test_unified_filter_components_avoid_native_select_regression():
    """Shared ProjectFilterSelect and PlanExecute DeviceFilterBar must use SearchableSelect."""
    project = (
        SourceGuard.of_repo_path(PROJECT_FILTER_SELECT)
        .anchored("export function ProjectFilterSelect(")
    )
    project.assert_present("SearchableSelect", why="页面级项目筛选必须走 SearchableSelect")
    project.assert_absent("<select", why="不得回潮为原生 select")

    plan_execute_bar = (
        SourceGuard.of_repo_path(DEVICE_FILTER_BAR)
        .anchored("export function DeviceFilterBar(")
    )
    plan_execute_bar.assert_present(
        "SearchableSelect", why="PlanExecute 设备筛选条必须走 SearchableSelect"
    )
    plan_execute_bar.assert_present(
        "FilterSearchInput", why="PlanExecute 设备筛选条必须保留搜索输入"
    )
    plan_execute_bar.assert_absent("<select", why="不得回潮为原生 select")


def test_device_and_plan_execute_filter_options_respect_active_status_scope():
    """Dropdown options in Devices and PlanExecute pages must stay scoped to active status/readiness."""
    device_table = (
        SourceGuard.of_repo_path(EXPANDABLE_DEVICE_TABLE)
        .anchored("const devicesInStatusAndTagScope = useMemo(")
    )
    device_table.assert_present(
        "devicesInStatusAndTagScope",
        why="设备页下拉选项必须先收口到 status/tag 作用域",
    )
    device_table.assert_present(
        "devicesInOptionScope",
        why="设备页下拉选项必须再收口到 host 作用域",
    )

    plan_execute = (
        SourceGuard.of_repo_path(PLAN_EXECUTE_PAGE)
        .anchored(
            "!readyOnly || (isSchedulable(device) && Boolean(deviceReadinessByDeviceId.get(device.id)?.ready))",
        )
    )
    plan_execute.assert_present(
        "devicesInOptionScope",
        why="PlanExecute 下拉选项必须走 readiness 作用域列表",
    )
    plan_execute.assert_present(
        "readyOnly",
        why="PlanExecute 选项作用域必须尊重仅就绪开关",
    )


# ---------------------------------------------------------------------------
# 前端系统性治理静态契约守卫（#4117 U1-3 · 只减不增白名单）
# 契约权威：docs/design/2026-11-23-frontend-ui-governance-contract.md
# ---------------------------------------------------------------------------

_RE_NATIVE_DIALOG = re.compile(r"\bwindow\.(?:confirm|prompt|alert)\s*\(")
_RE_RAW_TABLE = re.compile(r"<table\b")
_RE_CLICKABLE_TR = re.compile(r"<TableRow\b[^>]*\bonClick\s*=", re.DOTALL)
_RE_ARIA_MODAL = re.compile(r"\baria-modal\s*=")
_RE_HARDCODED_PALETTE = re.compile(
    r"\b(?:bg|text|border)-(?:red|green|blue|amber|yellow|slate|gray|zinc|neutral"
    r"|stone|emerald|rose|orange|indigo|violet|purple|sky|cyan|teal|pink)-\d{2,3}\b"
)

# 以下 allowlist 均为 Wave 3 / Wave 4 待治理存量清单，只减不增；
# 每完成一页治理必须同步从对应集合中移除该文件。
_NATIVE_DIALOG_ALLOWLIST = frozenset(
    {
        "pages/projects/ProjectDetailPage.tsx",
        "pages/projects/ProjectsPage.tsx",
        "pages/suites/TestSuiteDetailPage.tsx",
    }
)
_RAW_TABLE_ALLOWLIST = frozenset(
    {
        "pages/suites/TestSuiteDetailPage.tsx",
    }
)
# U4-1ab（#4117）已将全部可点击行收敛至 ClickableRow，存量基线为 0。
_CLICKABLE_TR_ALLOWLIST: frozenset[str] = frozenset()
# U1-1（#4122）已将全部 3 处旧手写抽屉收敛至 ui/sheet.tsx，存量基线为 0。
_BESPOKE_ARIA_MODAL_ALLOWLIST: frozenset[str] = frozenset()
_HARDCODED_PALETTE_ALLOWLIST = frozenset(
    {
        "pages/projects/components/MapModelsDialog.tsx",
    }
)
_PAGE_CONTAINER_EXEMPT = frozenset(
    {
        "pages/auth/LoginPage.tsx",
        "pages/auth/RegisterPage.tsx",
    }
)
# U4-1ab（#4117）已将 PlanRunDetailPage 纳入 PageContainer，存量基线为 0。
_MISSING_PAGE_CONTAINER_ALLOWLIST: frozenset[str] = frozenset()


def _iter_prod_tsx_files() -> list[Path]:
    files = sorted(
        p
        for p in FRONTEND_SRC.rglob("*.tsx")
        if not p.name.endswith(".test.tsx") and not p.name.endswith(".spec.tsx")
    )
    assert len(files) >= 50, f"前端 tsx 扫描集异常偏小（{len(files)}），疑似路径漂移"
    return files


def _assert_shrink_only_allowlist(
    *,
    rule: str,
    found: set[str],
    allowlist: frozenset[str],
    remediation: str,
) -> None:
    unexpected = sorted(found - allowlist)
    stale = sorted(allowlist - found)
    assert not unexpected, (
        f"[{rule}] 禁止新增违规文件：{unexpected}。修复指引：{remediation}"
    )
    assert not stale, (
        f"[{rule}] 以下存量白名单文件已修复，请同步从 allowlist 删除以保持只减不增：{stale}"
    )


def test_ui_governance_no_new_native_browser_dialogs():
    """禁止新增 window.confirm / prompt / alert，统一改用 AlertDialog（#4117）。"""
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in _iter_prod_tsx_files()
        if _RE_NATIVE_DIALOG.search(p.read_text(encoding="utf-8"))
    }
    _assert_shrink_only_allowlist(
        rule="no-native-dialog",
        found=found,
        allowlist=_NATIVE_DIALOG_ALLOWLIST,
        remediation="请改用 @/components/ui/alert-dialog 或 Dialog。",
    )


def test_ui_governance_no_new_raw_html_tables():
    """禁止在 ui/table.tsx 之外新增裸 <table>（#4117）。"""
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in _iter_prod_tsx_files()
        if p.relative_to(FRONTEND_SRC).as_posix() != "components/ui/table.tsx"
        and _RE_RAW_TABLE.search(p.read_text(encoding="utf-8"))
    }
    _assert_shrink_only_allowlist(
        rule="no-raw-table",
        found=found,
        allowlist=_RAW_TABLE_ALLOWLIST,
        remediation="请改用 @/components/ui/table 原语（Table / TableHeader / TableBody 等）。",
    )


def test_ui_governance_no_new_clickable_table_rows_without_clickable_row():
    """禁止新增不可键盘访问的 <TableRow onClick=...>（#3264 / #4117）。"""
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in _iter_prod_tsx_files()
        if p.relative_to(FRONTEND_SRC).as_posix() != "components/ui/clickable-row.tsx"
        and _RE_CLICKABLE_TR.search(p.read_text(encoding="utf-8"))
    }
    _assert_shrink_only_allowlist(
        rule="no-clickable-table-row",
        found=found,
        allowlist=_CLICKABLE_TR_ALLOWLIST,
        remediation="整行可点击必须改用 @/components/ui/clickable-row（ClickableRow）。",
    )


def test_ui_governance_no_bespoke_aria_modal_outside_ui_primitives():
    """禁止在 components/ui/ 之外手写 aria-modal 抽屉或弹窗（#3260 / #4117）。"""
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in _iter_prod_tsx_files()
        if not p.relative_to(FRONTEND_SRC).as_posix().startswith("components/ui/")
        and _RE_ARIA_MODAL.search(p.read_text(encoding="utf-8"))
    }
    _assert_shrink_only_allowlist(
        rule="no-bespoke-aria-modal",
        found=found,
        allowlist=_BESPOKE_ARIA_MODAL_ALLOWLIST,
        remediation="请改用 @/components/ui/sheet、dialog 或 alert-dialog。",
    )


def test_ui_governance_no_new_hardcoded_tailwind_palette_colors():
    """禁止新增硬编码 Tailwind 调色板色阶（须走语义 token，#4117）。"""
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in _iter_prod_tsx_files()
        if _RE_HARDCODED_PALETTE.search(p.read_text(encoding="utf-8"))
    }
    _assert_shrink_only_allowlist(
        rule="no-hardcoded-palette",
        found=found,
        allowlist=_HARDCODED_PALETTE_ALLOWLIST,
        remediation="请改用语义 token（如 bg-muted / text-destructive-text / border-warning 等）。",
    )


def test_ui_governance_routed_pages_use_page_container():
    """非认证路由页必须使用 PageContainer 统一页宽与内边距（#4117）。"""
    page_files = sorted(
        p
        for p in (FRONTEND_SRC / "pages").rglob("*Page.tsx")
        if not p.name.endswith(".test.tsx") and not p.name.endswith(".spec.tsx")
    )
    assert len(page_files) >= 20, f"路由页扫描集异常偏小（{len(page_files)}）"
    found = {
        p.relative_to(FRONTEND_SRC).as_posix()
        for p in page_files
        if p.relative_to(FRONTEND_SRC).as_posix() not in _PAGE_CONTAINER_EXEMPT
        and "PageContainer" not in p.read_text(encoding="utf-8")
    }
    _assert_shrink_only_allowlist(
        rule="require-page-container",
        found=found,
        allowlist=_MISSING_PAGE_CONTAINER_ALLOWLIST,
        remediation="路由主页面请使用 @/components/layout 的 PageContainer。",
    )
