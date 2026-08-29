import subprocess
from typing import List, Optional

import pytest

from backend.agent.operation_scheduler import OperationScheduler
from backend.agent.pipeline_engine import PipelineEngine


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
