"""AI 助手授权单测（无 PG）。"""

import pytest
from types import SimpleNamespace

from backend.services.ai_assistant.authz import (
    ToolAuthorizationError,
    assert_user_may_invoke_tool,
    user_may_invoke_tool,
)
from backend.services.ai_assistant.tools import TOOLS


class TestUserMayInvokeTool:
    def test_admin_only_requires_admin_role(self):
        spec = TOOLS["scan_script_catalog"]
        assert user_may_invoke_tool(SimpleNamespace(role="admin", is_active="Y"), spec)
        assert not user_may_invoke_tool(SimpleNamespace(role="user", is_active="Y"), spec)

    def test_login_user_tool_allowed_for_non_admin(self):
        spec = TOOLS["reload_agent_config"]
        assert user_may_invoke_tool(SimpleNamespace(role="user", is_active="Y"), spec)

    def test_assert_raises_for_denied(self):
        with pytest.raises(ToolAuthorizationError):
            assert_user_may_invoke_tool(
                SimpleNamespace(role="user", is_active="Y"),
                TOOLS["test_notification_channel"],
            )

    def test_disabled_account_denied_even_for_admin(self):
        """R13-F01 (#1213): is_active='N' 的账号即便 role=admin 也不得执行。"""
        spec = TOOLS["scan_script_catalog"]
        assert not user_may_invoke_tool(
            SimpleNamespace(role="admin", is_active="N"), spec
        )
        assert not user_may_invoke_tool(
            SimpleNamespace(role="user", is_active="N"),
            TOOLS["reload_agent_config"],
        )

    def test_missing_is_active_denied(self):
        """缺字段视同停用（防御性：执行面不得因字段缺失放行）。"""
        assert not user_may_invoke_tool(
            SimpleNamespace(role="admin"),
            TOOLS["scan_script_catalog"],
        )
