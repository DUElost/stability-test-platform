"""dedup → Jira 端点纯函数单测（ADR-0025 §10）。

只测 config 解析 + argv 拼装（无 DB / 无 app）。
端点 HTTP 层（鉴权/multipart/RunConsole 调用）的集成测试见 test_dedup_jira_endpoints.py。
"""

from __future__ import annotations

import pytest

from backend.api.routes.dedup import resolve_vendor_tool, build_jira_argv


def test_resolve_vendor_tool_none_when_unset(monkeypatch):
    monkeypatch.delenv("STP_JIRA_TRANSSION_PYTHON", raising=False)
    monkeypatch.delenv("STP_JIRA_TRANSSION_DIR", raising=False)
    assert resolve_vendor_tool("transsion") is None


def test_resolve_vendor_tool_reads_env(monkeypatch):
    monkeypatch.setenv("STP_JIRA_TINNO_PYTHON", "/opt/tinno/venv38/bin/python")
    monkeypatch.setenv("STP_JIRA_TINNO_DIR", "/opt/tinno/tool")
    tool = resolve_vendor_tool("tinno")
    assert tool == {"python": "/opt/tinno/venv38/bin/python", "dir": "/opt/tinno/tool"}


def test_build_argv_upload_list():
    argv = build_jira_argv(
        "transsion", "upload_list", "/tool", "/py",
        input_xls="/data/Result.xls",
    )
    assert argv[0] == "/py"
    assert argv[1].endswith("generate_transsion_jira_upload_list.py")
    assert "--add-main-excel" in argv
    assert "/data/Result.xls" in argv


def test_build_argv_create_dry_run_flag():
    argv_dry = build_jira_argv(
        "tinno", "create", "/tool", "/py",
        input_xls="/data/JIRA_Upload_List.xlsx", dry_run=True,
    )
    assert argv_dry[1].endswith("create_tinno_jira_batch_from_excel.py")
    assert "--add-excel-file" in argv_dry
    assert "/data/JIRA_Upload_List.xlsx" in argv_dry
    assert "--dry-run" in argv_dry

    argv_real = build_jira_argv(
        "tinno", "create", "/tool", "/py",
        input_xls="/data/JIRA_Upload_List.xlsx", dry_run=False,
    )
    assert "--dry-run" not in argv_real


def test_build_argv_create_with_reporter():
    argv = build_jira_argv(
        "transsion", "create", "/tool", "/py",
        input_xls="/data/JIRA_Upload_List.xlsx", dry_run=True, reporter="alice",
    )
    assert "--reporter" in argv
    assert "alice" in argv


def test_build_argv_create_without_reporter_omits_flag():
    argv = build_jira_argv(
        "transsion", "create", "/tool", "/py",
        input_xls="/data/JIRA_Upload_List.xlsx", dry_run=True, reporter=None,
    )
    assert "--reporter" not in argv


def test_build_argv_upload_list_ignores_reporter():
    argv = build_jira_argv(
        "transsion", "upload_list", "/tool", "/py",
        input_xls="/data/Result.xls", reporter="alice",
    )
    assert "--reporter" not in argv
