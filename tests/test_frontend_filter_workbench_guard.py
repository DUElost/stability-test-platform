"""Static regression guards for unified frontend filter workbench components."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_unified_filter_components_avoid_native_select_regression():
    """Shared ProjectFilterSelect and PlanExecute DeviceFilterBar must use SearchableSelect."""
    project_select = (
        REPO_ROOT / "frontend/src/components/project/ProjectFilterSelect.tsx"
    ).read_text(encoding="utf-8")
    assert "SearchableSelect" in project_select
    assert "<select" not in project_select

    plan_execute_bar = (
        REPO_ROOT
        / "frontend/src/components/execution/plan-execute/DeviceFilterBar.tsx"
    ).read_text(encoding="utf-8")
    assert "SearchableSelect" in plan_execute_bar
    assert "FilterSearchInput" in plan_execute_bar
    assert "<select" not in plan_execute_bar


def test_device_and_plan_execute_filter_options_respect_active_status_scope():
    """Dropdown options in Devices and PlanExecute pages must stay scoped to active status/readiness."""
    device_table = (
        REPO_ROOT / "frontend/src/components/device/ExpandableDeviceTable.tsx"
    ).read_text(encoding="utf-8")
    assert "devicesInStatusAndTagScope" in device_table
    assert "devicesInOptionScope" in device_table

    plan_execute = (
        REPO_ROOT / "frontend/src/pages/execution/PlanExecutePage.tsx"
    ).read_text(encoding="utf-8")
    assert "devicesInOptionScope" in plan_execute
    assert "readyOnly" in plan_execute
