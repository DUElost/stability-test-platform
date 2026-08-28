"""AI 助手工具注册表校验单测（无 PG）——安全边界的行为锁定。"""

import sys

import pytest

from backend.services.ai_assistant import tools


class TestRegistryShape:
    def test_tiers(self):
        t1 = {n for n, s in tools.TOOLS.items() if s.tier == "T1"}
        assert t1 == {"run_quality_gate", "run_agent_tests", "run_gov_checks"}

    def test_t3_absent_by_construction(self):
        names = " ".join(tools.TOOLS).lower()
        assert "hot_update" not in names
        assert "shell" not in names.replace("run_shell", "")  # 无任意 shell 工具

    def test_whitelistable_only_low_risk_t2(self):
        for spec in tools.TOOLS.values():
            if spec.whitelistable:
                assert spec.tier == "T2"
        assert {n for n, s in tools.TOOLS.items() if s.whitelistable} == {
            "test_notification_channel"
        }

    def test_openai_tools_payload_shape(self):
        payload = tools.to_openai_tools()
        assert len(payload) == len(tools.TOOLS)
        for entry in payload:
            assert entry["type"] == "function"
            assert entry["function"]["parameters"]["type"] == "object"


class TestRoleFiltering:
    def test_admin_gets_all_non_admin_excludes_admin_only(self):
        admin = tools.allowed_tool_names(is_admin=True)
        normal = tools.allowed_tool_names(is_admin=False)
        assert "query_recent_audit_logs" in admin
        assert "get_settings_overview" in admin
        # admin-only 端点的镜像工具不得对普通用户开放（PR-Agent gate 越权修复）
        assert "query_recent_audit_logs" not in normal
        assert "get_settings_overview" not in normal
        # 普通用户仍可用观测类其余工具
        assert "query_hosts" in normal and "query_plan_runs" in normal

    def test_openai_payload_filtered(self):
        payload = tools.to_openai_tools(tools.allowed_tool_names(is_admin=False))
        names = {e["function"]["name"] for e in payload}
        assert "query_recent_audit_logs" not in names
        assert len(names) == len(tools.TOOLS) - 2


class TestRunConsolePlans:
    def test_quality_gate_cmd_is_argv(self):
        plan = tools.build_runconsole_plan("run_quality_gate", {"profile": "quick"})
        assert plan.cmd == [sys.executable, "scripts/run_gates.py", "check:quick"]
        assert plan.run_key == "ai-gate:quick"
        # check:pr 含 agent-tests：质量门禁同样必须显式覆盖生产 DATABASE_URL
        # （PR-Agent gate 复评发现；与 run_agent_tests 同源约束）
        assert plan.env["DATABASE_URL"] == tools.AGENT_TEST_ENV_OVERRIDE["DATABASE_URL"]
        assert plan.env["TESTING"] == "1"
        # cwd 是仓库根（worktree 或主树皆可）：以门禁脚本存在性判定
        assert (plan.cwd / "scripts" / "run_gates.py").exists()

    def test_quality_gate_profile_enum(self):
        with pytest.raises(tools.ToolValidationError):
            tools.build_runconsole_plan("run_quality_gate", {"profile": "full;rm -rf /"})
        with pytest.raises(tools.ToolValidationError):
            tools.build_runconsole_plan("run_quality_gate", {})

    def test_agent_tests_env_overrides_production_db(self):
        """H1 结构性断言：四键显式注入，DATABASE_URL 为占位值而非生产串。"""
        plan = tools.build_runconsole_plan("run_agent_tests", {})
        assert plan.env["TESTING"] == "1"
        assert plan.env["DATABASE_URL"] == tools.AGENT_TEST_ENV_OVERRIDE["DATABASE_URL"]
        assert "postgres:postgres@localhost:5432/stability_test" in plan.env["DATABASE_URL"]
        assert plan.run_key == "ai-agent-tests"

    def test_agent_tests_path_traversal_rejected(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "../core/database.py"):
            with pytest.raises(tools.ToolValidationError):
                tools.build_runconsole_plan("run_agent_tests", {"file_path": bad})

    def test_agent_tests_unknown_file_rejected(self):
        with pytest.raises(tools.ToolValidationError):
            tools.build_runconsole_plan("run_agent_tests", {"file_path": "no_such_file.py"})

    def test_agent_tests_valid_file_within_dir(self):
        plan = tools.build_runconsole_plan("run_agent_tests", {"file_path": ""})
        assert "backend/agent/tests" in str(plan.cmd[3])

    def test_gov_checks_only_surface(self):
        plan = tools.build_runconsole_plan("run_gov_checks", {"check": "surface"})
        assert plan.cmd[-1] == "--check"
        with pytest.raises(tools.ToolValidationError):
            tools.build_runconsole_plan("run_gov_checks", {"check": "pollution"})

    def test_unknown_tool_rejected(self):
        with pytest.raises(tools.ToolValidationError):
            tools.build_runconsole_plan("rm_rf_everything", {})


class TestSearchDocs:
    def test_search_docs_no_db(self):
        result = tools._search_docs(None, {"query": "ADR-0031", "limit": 3})
        assert "ADR" in result

    def test_search_docs_requires_query(self):
        with pytest.raises(tools.ToolValidationError):
            tools._search_docs(None, {"query": ""})
