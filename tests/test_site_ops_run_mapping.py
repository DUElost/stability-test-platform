"""`LocalOps.run` 的三态映射（#2208 / #2277）。

外部命令失败不是异常而是一种**结果**（与 shell 的退出码同语义）：命令不存在 127、
无权限 126、超时 124。此前 FileNotFoundError 会把整次安装崩成 traceback（#2208），
TimeoutExpired 同样（#2277：挂住的 NFS/SSH 类命令）——调用方准备好的「外部命令失败」
检查永远走不到，报告也不产出。
"""

from __future__ import annotations

import subprocess

import pytest

from tools.site_config import ops as ops_module
from tools.site_config.ops import COMMAND_TIMEOUT_SECONDS, LocalOps


@pytest.mark.parametrize(
    "error, expected_rc, needle",
    [
        (FileNotFoundError(2, "No such file or directory", "exportfs"), 127, "command not found"),
        (PermissionError(13, "Permission denied", "mount"), 126, "permission denied"),
        (
            subprocess.TimeoutExpired(cmd="mount", timeout=COMMAND_TIMEOUT_SECONDS),
            124,
            "timed out",
        ),
    ],
    ids=["missing", "denied", "timeout"],
)
def test_run_maps_failures_to_shell_exit_codes(monkeypatch, error, expected_rc, needle):
    def boom(*args, **kwargs):
        raise error

    monkeypatch.setattr(ops_module.subprocess, "run", boom)

    result = LocalOps().run(["exportfs", "-ra"])

    assert result.returncode == expected_rc
    assert needle in result.stderr
    assert result.argv == ("exportfs", "-ra"), "结果里保留原 argv，便于调用方归因"
