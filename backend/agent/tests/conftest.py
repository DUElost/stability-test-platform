import subprocess
from typing import List, Optional

import pytest


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
