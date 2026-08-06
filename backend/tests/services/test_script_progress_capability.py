"""#136：stall_seconds 配置门禁的白名单单元测试。"""

from backend.services.script_progress_capability import (
    PROGRESS_CAPABLE_SCRIPTS,
    script_supports_progress,
)


class TestScriptProgressCapability:
    def test_known_capable_versions(self):
        assert script_supports_progress("monkey_setup", "v2.3.1")
        assert script_supports_progress("monkey_setup", "v2.3.2")
        assert script_supports_progress("monkey_setup", "v2.3.3")
        assert script_supports_progress("monkey_setup", "v2.3.4")
        assert script_supports_progress("flash_firmware", "v1.1.0")

    def test_legacy_and_unknown_versions_are_not_capable(self):
        assert not script_supports_progress("monkey_setup", "v2.2.0")
        assert not script_supports_progress("monkey_setup", "v2.3.0")
        assert not script_supports_progress("monkey_setup", "v2.4.0")
        assert not script_supports_progress("unknown_script", "v1.0.0")

    def test_version_without_v_prefix_matches(self):
        """#136 格式回归：plan_step 存无 v（2.3.4），白名单登记带 v——两种都匹配。"""
        assert script_supports_progress("monkey_setup", "2.3.1")
        assert script_supports_progress("monkey_setup", "2.3.4")
        assert not script_supports_progress("monkey_setup", "2.2.0")

    def test_registry_is_non_empty_and_normalised(self):
        assert PROGRESS_CAPABLE_SCRIPTS
        for name, version in PROGRESS_CAPABLE_SCRIPTS:
            assert name and version
            assert name == name.strip()
            assert version == version.strip()
