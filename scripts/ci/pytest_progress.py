"""CI-only case-start lines, emitted before fixture setup and explicitly flushed."""
import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item) -> None:
    reporter = item.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(f"[pytest-start] {item.nodeid}")
        reporter.flush()
