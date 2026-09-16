"""#2372：`plan_run_abort` 的 import 契约——**clean-env 可 import** 且 **emit 缝可 patch**。

两个约束必须同时成立：

1. 不设 `JWT_SECRET_KEY` 也能 import 本模块——agent collect 的 clean-env 路径会 import 它
   （#2270 的 `plan_dispatcher_sync → run_abort_pending`；#2350 的成因）；
2. `schedule_emit` / `schedule_agent_control_fanout` 仍是**模块属性**——控制面用例的
   patch 目标（把它们挪进调用点函数体 = 11 条确定性红，即本单的成因）。

只满足一条就会重演 #2270 → #2350 → #2372 的往复。本文件把这段往复固化成门禁：
**要动这里的 import 结构，先读这两个约束。**
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_PROBE = (
    "import sys\n"
    "import backend.services.plan_run_abort as m\n"
    "assert callable(m.schedule_emit), 'emit 缝不在模块命名空间（patch 目标会消失）'\n"
    "assert callable(m.schedule_agent_control_fanout), 'fanout 缝缺失'\n"
    "assert 'backend.realtime.socketio_server' not in sys.modules, (\n"
    "    'import 期拉起了 socketio_server → clean-env collect 会因 JWT_SECRET_KEY 失败'\n"
    ")\n"
)


def test_plan_run_abort_import_contract():
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET_KEY"}
    # 显式覆盖（不是 setdefault）：避免把本机环境的真实连接串带进子进程，
    # 也避免依赖运行者恰好设了什么。
    env["DATABASE_URL"] = "postgresql+psycopg://probe:probe@127.0.0.1:5432/stp_probe"

    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
