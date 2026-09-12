# -*- coding: utf-8 -*-
"""pytest 进程内测试容器清理兜底（#1492）。

背景：``backend/tests/conftest.py`` 在未设 ``TEST_DATABASE_URL`` 时起
per-process ``postgres:16`` 容器；testcontainers 的 ryuk 在本机未可靠回收
（实测残留 36 个、最老 >2 周，见 #1482）——本模块在**进程可控退出路径**
上主动 ``container.stop()``：

- ``stop()``：幂等、best-effort（失败不外溢），正常结束/信号/atexit 多路径
  重复触发只停一次；
- ``register()``：挂 ``atexit`` + SIGTERM/SIGINT 处理器；信号路径停容器后
  **保持原退出语义**（前一个处理器可调用则链回，否则恢复默认并原样重发信号）；
- SIGKILL 无法拦截 —— 由 #1482 的巡检工具（``check_test_containers.py``）
  兜底。
"""

from __future__ import annotations

import atexit
import signal
import threading
from typing import Any


class ContainerCleanup:
    """单个测试容器的清理守卫（幂等 + 信号语义保持）。"""

    def __init__(self, container: Any) -> None:
        self._container = container
        self._stopped = False
        self._lock = threading.Lock()
        self._registered = False

    @property
    def stopped(self) -> bool:
        return self._stopped

    def stop(self) -> bool:
        """停容器；返回本次是否真正执行了 stop（重复调用返回 False）。

        best-effort：``container.stop()`` 的异常只吞掉（进程可能正在退出），
        但会标记 stopped 以避免在信号处理器里反复重试卡住退出。
        """
        if self._container is None:
            return False
        with self._lock:
            if self._stopped:
                return False
            self._stopped = True
        try:
            self._container.stop()
        except Exception:  # noqa: BLE001 - 退出路径不允许外溢
            pass
        return True

    def _signal_handler(self, signum: int, frame: Any, *, signal_module, previous: Any) -> None:
        self.stop()
        # 恢复原处理器并保持原退出语义
        try:
            signal_module.signal(signum, previous)
        except Exception:  # noqa: BLE001
            pass
        if callable(previous):
            previous(signum, frame)
            return
        signal_module.raise_signal(signum)

    def register(self, *, atexit_module=atexit, signal_module=signal) -> None:
        """挂 atexit + SIGTERM/SIGINT 清理（只挂一次）。"""
        if self._container is None or self._registered:
            return
        self._registered = True
        atexit_module.register(self.stop)
        for signum in (signal_module.SIGTERM, signal_module.SIGINT):
            previous = signal_module.getsignal(signum)

            def handler(signum_, frame_, *, _sig=signum, _prev=previous):
                self._signal_handler(
                    signum_, frame_, signal_module=signal_module, previous=_prev,
                )

            try:
                signal_module.signal(signum, handler)
            except Exception:  # noqa: BLE001 - 非主线程等场景跳过信号挂载
                continue
