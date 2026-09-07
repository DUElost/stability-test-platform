"""#941 回归：conftest 解析的测试库地址必须写回 TEST_DATABASE_URL。

租约（test_device_leases_unique / test_lease_manager）与 abort-reaper 等
PG-only 测试在模块 import 时以 ``os.getenv("TEST_DATABASE_URL")`` 判方言；
conftest 的 testcontainers 兜底路径原先只写 DATABASE_URL，导致「不预设
TEST_DATABASE_URL」的默认运行路径整组 skip——尽管实际连的就是 PostgreSQL。
"""

import os

from backend.tests import conftest as tests_conftest


def test_resolved_test_database_url_exported_to_env():
    for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
        assert os.environ.get(key) == tests_conftest.TEST_DATABASE_URL, (
            f"conftest 解析结果未写回 {key}——PG-only 测试会误判方言整组 skip（#941）"
        )
    # conftest 不提供 SQLite 兜底：解析结果恒为 PostgreSQL（显式配置或容器）
    assert tests_conftest.TEST_DATABASE_URL.startswith("postgresql")
