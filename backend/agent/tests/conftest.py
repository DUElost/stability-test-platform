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
