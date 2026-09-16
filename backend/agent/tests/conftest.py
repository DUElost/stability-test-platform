import os
import subprocess
from typing import List, Optional

# R15-F03 (#1295)：直接运行 Agent 测试（文档推荐的 `python -m pytest`）时，
# 导入 backend.agent.* 会经 backend.core.database 的模块级
# resolve_database_url() 回落到仓库根 .env.backend（生产配置）。CI / run_gates
# 各自注入了环境，保护不到这个直接入口。这里在任何 agent 模块导入之前建立
# 安全边界：若未显式提供 DATABASE_URL，给出一个明确的本地隔离占位地址
# （Agent 单测用 SQLite / mock，不会真的连它），从而杜绝读取生产配置。
AGENT_TEST_DATABASE_URL = (
    "postgresql+psycopg://agent_test:agent_test@127.0.0.1:5432/agent_test"
)
os.environ.setdefault("DATABASE_URL", AGENT_TEST_DATABASE_URL)

# 同一条边界也必须覆盖 JWT_SECRET_KEY（#2428）：`backend/core/security.py` 在**导入期**
# 硬检查它，CI 的 pr-agent-tests job 与 run_gates 的 agent-tests gate 都注入了这个值，
# 于是「直接 `python -m pytest backend/agent/tests`」这个文档推荐入口本地恒红 23 例
# （test_saq_scan_pipeline 16 / test_p3_3_multi_instance 4 / test_legacy_tool_cleanup 2 /
# test_cron_scheduler 1），改动者分不清「我改坏了」还是「环境本红」，只能 stash 复跑自证。
# 这些用例断言的内容与签名密钥毫无关系——缺的只是一个测试用占位值，正该由本 conftest
# 给出。用 setdefault：调用方显式注入的值仍然优先（CI 侧 job env 不变）。
AGENT_TEST_JWT_SECRET_KEY = "agent-test-secret-key-not-for-production-32b"
os.environ.setdefault("JWT_SECRET_KEY", AGENT_TEST_JWT_SECRET_KEY)

import pytest  # noqa: E402

from backend.agent.operation_scheduler import OperationScheduler  # noqa: E402
from backend.agent.pipeline_engine import PipelineEngine  # noqa: E402


@pytest.fixture(autouse=True)
def _inject_default_operation_scheduler(monkeypatch):
    """Production Agents always wire OperationScheduler (#521 fail-fast).

    Tests that construct PipelineEngine without one get a real scheduler so
    lifecycle steps are not rejected with operation_scheduler_required.
  """
    original_init = PipelineEngine.__init__

    def _init_with_scheduler(self, *args, **kwargs):
        if kwargs.get("operation_scheduler") is None:
            kwargs["operation_scheduler"] = OperationScheduler()
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(PipelineEngine, "__init__", _init_with_scheduler)


@pytest.fixture
def completed_process_factory():
    """快速构造 subprocess.CompletedProcess。"""

    def _factory(
        stdout: str = "",
        stderr: str = "",
        returncode: int = 0,
        args: Optional[List[str]] = None,
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=args or ["adb"],
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        )

    return _factory


@pytest.fixture
def lease_env(monkeypatch):
    """租约域 env 覆盖助手（ADR-0042 P1）：写 env 后清 Settings 缓存，测试末再清。

    迁移后不再依赖「构造续租器时现读 env」——Settings 是惰性缓存视图，改 env
    必须伴随 `reset_agent_settings_caches()`，否则读到旧缓存。
    """

    from backend.agent.settings import reset_agent_settings_caches

    def _set(name: str, value: str) -> None:
        monkeypatch.setenv(name, value)
        reset_agent_settings_caches()

    reset_agent_settings_caches()
    yield _set
    reset_agent_settings_caches()


@pytest.fixture
def disk_env(monkeypatch):
    """磁盘/归档域 env 覆盖助手（ADR-0042 P2）：写/删 env 后清 Agent Settings 缓存。"""

    from backend.agent.settings import reset_agent_settings_caches

    class _Env:
        @staticmethod
        def set(name: str, value: str) -> None:
            monkeypatch.setenv(name, value)
            reset_agent_settings_caches()

        @staticmethod
        def unset(name: str) -> None:
            monkeypatch.delenv(name, raising=False)
            reset_agent_settings_caches()

    reset_agent_settings_caches()
    yield _Env
    reset_agent_settings_caches()


@pytest.fixture
def heartbeat_env(monkeypatch):
    """心跳/协调/注册域 env 覆盖助手（ADR-0042 P2 #3）。

    与 `disk_env` 同形：写/删 env 后清 Agent Settings 缓存——迁移后
    HeartbeatThread / HostRunCoordinator 的旋钮来自惰性缓存视图，
    改 env 不同步 reset 会读到旧值。
    """

    from backend.agent.settings import reset_agent_settings_caches

    class _Env:
        @staticmethod
        def set(name: str, value: str) -> None:
            monkeypatch.setenv(name, value)
            reset_agent_settings_caches()

        @staticmethod
        def unset(name: str) -> None:
            monkeypatch.delenv(name, raising=False)
            reset_agent_settings_caches()

    reset_agent_settings_caches()
    yield _Env
    reset_agent_settings_caches()

