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
        assert user_may_invoke_tool(SimpleNamespace(role="admin"), spec)
        assert not user_may_invoke_tool(SimpleNamespace(role="user"), spec)

    def test_login_user_tool_allowed_for_non_admin(self):
        spec = TOOLS["reload_agent_config"]
        assert user_may_invoke_tool(SimpleNamespace(role="user"), spec)

    def test_assert_raises_for_denied(self):
        with pytest.raises(ToolAuthorizationError):
            assert_user_may_invoke_tool(
                SimpleNamespace(role="user"), TOOLS["test_notification_channel"]
            )
