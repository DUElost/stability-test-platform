"""ADR-0033 Phase A1：JiraVendorEngine 行为契约。"""

from __future__ import annotations

import pytest

from backend.services.jira_vendor import (
    JiraVendorEngine,
    StabilityJiraAutomationEngine,
    build_jira_argv,
    get_jira_vendor_engine,
    resolve_vendor_tool,
)


def test_default_engine_is_stability_jira():
    engine = get_jira_vendor_engine()
    assert isinstance(engine, StabilityJiraAutomationEngine)
    assert isinstance(engine, JiraVendorEngine)


def test_resolve_and_argv_via_engine(monkeypatch):
    monkeypatch.setenv("STP_JIRA_TRANSSION_PYTHON", "/py")
    monkeypatch.setenv("STP_JIRA_TRANSSION_DIR", "/tool")
    assert resolve_vendor_tool("transsion") == {"python": "/py", "dir": "/tool"}
    argv = build_jira_argv(
        "transsion",
        "upload_list",
        "/tool",
        "/py",
        input_xls="/x.xls",
        jira_project_key="K",
    )
    assert argv[:4] == ["/py", "/tool/generate_transsion_jira_upload_list.py", "--add-main-excel", "/x.xls"]
    assert argv[4:] == ["--set-project-key", "K"]


def test_unknown_stage_raises():
    with pytest.raises(ValueError, match="unknown jira stage"):
        build_jira_argv("tinno", "nope", "/tool", "/py", input_xls="/x.xls")


def test_engine_source_has_no_api_routes_import():
    """分层：services 不得引用 api.routes（与 check_layering 同口径）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "services" / "jira_vendor"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "backend.api.routes" not in text, path
