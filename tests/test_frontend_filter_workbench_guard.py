"""Static regression guards for unified frontend filter workbench components.

源扫描型否定断言必须经 SourceGuard 锚点先行（#2639）：否则被扫逻辑搬走后
`assert "<select" not in text` 会恒真，守卫仍绿却已空守。
"""

from __future__ import annotations

from tools.dev.source_anchor import SourceGuard

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
